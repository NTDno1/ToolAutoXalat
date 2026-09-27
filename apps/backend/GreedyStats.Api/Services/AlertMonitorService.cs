using System.Net.Http.Json;
using System.Text.Json;
using GreedyStats.Api.Data;
using GreedyStats.Api.Models;

namespace GreedyStats.Api.Services;

public sealed class AlertMonitorService : BackgroundService
{
    private readonly GreedyDatabase _database;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IConfiguration _configuration;
    private readonly ILogger<AlertMonitorService> _logger;
    private string? _observedResultRevision;
    private DateTimeOffset _resultRevisionObservedAtUtc;
    private string? _resultStallAlertedRevision;

    public AlertMonitorService(
        GreedyDatabase database,
        IHttpClientFactory httpClientFactory,
        IConfiguration configuration,
        ILogger<AlertMonitorService> logger)
    {
        _database = database;
        _httpClientFactory = httpClientFactory;
        _configuration = configuration;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var pollSeconds = _configuration.GetValue("Alerts:PollIntervalSeconds", 2);
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(Math.Max(1, pollSeconds)));
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await EvaluateScannerWatchdogAsync(stoppingToken);
                await EvaluateSystemEventAsync(stoppingToken);
            }
            catch (Exception exception) when (exception is not OperationCanceledException)
            {
                _logger.LogError(exception, "Alert monitor iteration failed");
            }
            if (!await timer.WaitForNextTickAsync(stoppingToken))
            {
                break;
            }
        }
    }

    private async Task EvaluateScannerWatchdogAsync(CancellationToken cancellationToken)
    {
        if (!_configuration.GetValue("Alerts:ScannerWatchdogEnabled", true))
        {
            return;
        }

        var scanner = await _database.GetScannerStatusAsync(cancellationToken);
        var now = DateTimeOffset.UtcNow;
        if (scanner.Status is "NOT_STARTED" or "STOPPED")
        {
            _observedResultRevision = null;
            _resultStallAlertedRevision = null;
            return;
        }

        var heartbeatLimit = Math.Max(
            15,
            _configuration.GetValue("Alerts:ScannerHeartbeatStaleSeconds", 60));
        if (scanner.LastHeartbeatUtc is { } heartbeat)
        {
            var heartbeatAge = (now - heartbeat).TotalSeconds;
            if (heartbeatAge >= heartbeatLimit)
            {
                var details = JsonSerializer.SerializeToElement(new
                {
                    scanner.SourceSerial,
                    scanner.Status,
                    scanner.CurrentRound,
                    heartbeatAgeSeconds = Math.Round(heartbeatAge, 1),
                    heartbeatStaleAfterSeconds = heartbeatLimit,
                    scanner.LastHeartbeatUtc
                });
                await _database.InsertEventAsync(new ScannerEventRequest(
                    $"backend:SCANNER_HEARTBEAT_STALLED:{heartbeat:yyyyMMddHHmmssfff}",
                    "CRITICAL",
                    "SCANNER_HEARTBEAT_STALLED",
                    "Scanner bị treo hoặc đã dừng cập nhật heartbeat",
                    details,
                    now), cancellationToken);
            }
        }

        var revision = scanner.LastResultRevision ?? "NO_RESULT";
        if (!string.Equals(_observedResultRevision, revision, StringComparison.Ordinal))
        {
            _observedResultRevision = revision;
            _resultRevisionObservedAtUtc = now;
            _resultStallAlertedRevision = null;
            return;
        }

        var resultLimit = Math.Max(
            60,
            _configuration.GetValue("Alerts:ResultProgressStaleSeconds", 360));
        var unchangedSeconds = (now - _resultRevisionObservedAtUtc).TotalSeconds;
        if (
            unchangedSeconds >= resultLimit
            && !string.Equals(_resultStallAlertedRevision, revision, StringComparison.Ordinal)
        )
        {
            var details = JsonSerializer.SerializeToElement(new
            {
                scanner.SourceSerial,
                scanner.Status,
                scanner.CurrentRound,
                scanner.LastResultId,
                scanner.LastResultRevision,
                unchangedSeconds = Math.Round(unchangedSeconds, 1),
                resultStaleAfterSeconds = resultLimit
            });
            await _database.InsertEventAsync(new ScannerEventRequest(
                $"backend:SCANNER_RESULT_STALLED:{scanner.SourceSerial}:{_resultRevisionObservedAtUtc:yyyyMMddHHmmssfff}",
                "ERROR",
                "SCANNER_RESULT_STALLED",
                $"Đã {Math.Round(unchangedSeconds / 60d, 1):0.#} phút chưa cập nhật được cầu mới",
                details,
                now), cancellationToken);
            _resultStallAlertedRevision = revision;
        }
    }

    private async Task EvaluateStreakAlertAsync(CancellationToken cancellationToken)
    {
        var vegetableThreshold = _configuration.GetValue("Alerts:VegetableStreakThreshold", 10);
        var meatThreshold = _configuration.GetValue("Alerts:MeatStreakThreshold", 3);
        var webhookUrl = await _database.ResolveAlertWebhookUrlAsync(_configuration, cancellationToken);
        var candidate = await _database.GetCurrentAlertCandidateAsync(
            vegetableThreshold,
            meatThreshold,
            cancellationToken);
        if (candidate is null)
        {
            return;
        }

        var subscribers = await _database.GetSubscribersAsync(true, cancellationToken);
        var configured = !string.IsNullOrWhiteSpace(webhookUrl);
        var retrySeconds = Math.Max(10, _configuration.GetValue("Alerts:WebhookRetrySeconds", 30));
        var status = await _database.EnsureAlertAsync(
            candidate, configured, retrySeconds, cancellationToken);
        if (!configured || status != "PENDING")
        {
            return;
        }

        var categoryName = candidate.Category == "MEAT" ? "Thịt" : "Rau";
        var payload = CreateEnvelope(
            candidate.AlertKey,
            "STREAK_ALERT",
            candidate.RuleCode,
            "WARNING",
            $"Bệt {categoryName} đạt {candidate.StreakLength} cầu",
            $"Đã xuất hiện {candidate.StreakLength} kết quả {categoryName} liên tục.",
            DateTimeOffset.UtcNow,
            new
            {
                deliveryMode = "BROADCAST",
                recipientCount = subscribers.Count,
                alert = candidate.Payload,
                recipients = subscribers.Select(item => new
                {
                    item.Id,
                    item.PhoneNumber,
                    item.DisplayName
                })
            });
        try
        {
            var client = _httpClientFactory.CreateClient("alert-webhook");
            using var response = await client.PostAsJsonAsync(webhookUrl, payload, cancellationToken);
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            await _database.CompleteAlertAsync(
                candidate.AlertKey,
                response.IsSuccessStatusCode ? "DELIVERED" : "RETRY",
                (int)response.StatusCode,
                Truncate(body),
                cancellationToken);
            _logger.LogInformation(
                "User webhook {AlertKey} completed with HTTP {StatusCode}",
                candidate.AlertKey,
                (int)response.StatusCode);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            await _database.CompleteAlertAsync(
                candidate.AlertKey,
                "RETRY",
                null,
                Truncate(exception.Message),
                cancellationToken);
            _logger.LogError(exception, "User webhook {AlertKey} failed", candidate.AlertKey);
        }
    }

    private async Task EvaluateSystemEventAsync(CancellationToken cancellationToken)
    {
        var retrySeconds = Math.Max(10, _configuration.GetValue("Alerts:WebhookRetrySeconds", 30));
        var bootstrapMaxAgeMinutes = Math.Max(
            1, _configuration.GetValue("Alerts:SystemEventBootstrapMaxAgeMinutes", 15));
        var candidate = await _database.GetNextSystemEventDeliveryAsync(
            retrySeconds, bootstrapMaxAgeMinutes, cancellationToken);
        if (candidate is null)
        {
            return;
        }
        var webhookUrl = await _database.ResolveAlertWebhookUrlAsync(_configuration, cancellationToken);
        var configured = !string.IsNullOrWhiteSpace(webhookUrl);
        var status = await _database.EnsureSystemEventDeliveryAsync(
            candidate.EventId, configured, retrySeconds, cancellationToken);
        if (!configured || status != "PENDING")
        {
            return;
        }

        var payload = CreateEnvelope(
            $"system-event:{candidate.EventId}",
            "SCANNER_SYSTEM_EVENT",
            candidate.EventCode,
            candidate.Severity,
            "Cầu chưa cập nhật sau 6 phút",
            candidate.Message,
            candidate.OccurredAtUtc,
            new
            {
                eventId = candidate.EventId,
                details = candidate.Details
            });
        try
        {
            var client = _httpClientFactory.CreateClient("alert-webhook");
            using var response = await client.PostAsJsonAsync(webhookUrl, payload, cancellationToken);
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            await _database.CompleteSystemEventDeliveryAsync(
                candidate.EventId,
                response.IsSuccessStatusCode ? "DELIVERED" : "RETRY",
                (int)response.StatusCode,
                Truncate(body),
                cancellationToken);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            await _database.CompleteSystemEventDeliveryAsync(
                candidate.EventId,
                "RETRY",
                null,
                Truncate(exception.Message),
                cancellationToken);
            _logger.LogError(exception, "Admin system-event webhook failed for event {EventId}", candidate.EventId);
        }
    }

    private static string Truncate(string value) =>
        value.Length > 2000 ? value[..2000] : value;

    private static object CreateEnvelope(
        string notificationId,
        string type,
        string eventCode,
        string severity,
        string title,
        string message,
        DateTimeOffset occurredAtUtc,
        object data) => new
        {
            schemaVersion = "1.0",
            source = "ToolAutoXalat",
            notificationId,
            type,
            eventCode,
            severity,
            title,
            message,
            occurredAtUtc,
            data
        };
}
