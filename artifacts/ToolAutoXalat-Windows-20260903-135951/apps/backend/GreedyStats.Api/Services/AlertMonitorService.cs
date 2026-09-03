using System.Net.Http.Json;
using GreedyStats.Api.Data;

namespace GreedyStats.Api.Services;

public sealed class AlertMonitorService : BackgroundService
{
    private readonly GreedyDatabase _database;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IConfiguration _configuration;
    private readonly ILogger<AlertMonitorService> _logger;

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
                await EvaluateStreakAlertAsync(stoppingToken);
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

    private async Task EvaluateStreakAlertAsync(CancellationToken cancellationToken)
    {
        var vegetableThreshold = _configuration.GetValue("Alerts:VegetableStreakThreshold", 15);
        var meatThreshold = _configuration.GetValue("Alerts:MeatStreakThreshold", 3);
        var webhookUrl = (_configuration["Alerts:UserWebhookUrl"] ??
                          _configuration["Alerts:WebhookUrl"])?.Trim();
        var candidate = await _database.GetCurrentAlertCandidateAsync(
            vegetableThreshold,
            meatThreshold,
            cancellationToken);
        if (candidate is null)
        {
            return;
        }

        var subscribers = await _database.GetSubscribersAsync(true, cancellationToken);
        var configured = !string.IsNullOrWhiteSpace(webhookUrl) && subscribers.Count > 0;
        var status = await _database.EnsureAlertAsync(candidate, configured, cancellationToken);
        if (!configured || status is "DELIVERED" or "FAILED")
        {
            return;
        }

        var payload = new
        {
            type = "STREAK_ALERT",
            deliveryMode = "BROADCAST",
            recipientCount = subscribers.Count,
            alert = candidate.Payload,
            recipients = subscribers.Select(item => new
            {
                item.Id,
                item.PhoneNumber,
                item.DisplayName
            })
        };
        try
        {
            var client = _httpClientFactory.CreateClient("alert-webhook");
            using var response = await client.PostAsJsonAsync(webhookUrl, payload, cancellationToken);
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            await _database.CompleteAlertAsync(
                candidate.AlertKey,
                response.IsSuccessStatusCode ? "DELIVERED" : "FAILED",
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
                "FAILED",
                null,
                Truncate(exception.Message),
                cancellationToken);
            _logger.LogError(exception, "User webhook {AlertKey} failed", candidate.AlertKey);
        }
    }

    private async Task EvaluateSystemEventAsync(CancellationToken cancellationToken)
    {
        var candidate = await _database.GetNextSystemEventDeliveryAsync(cancellationToken);
        if (candidate is null)
        {
            return;
        }
        var webhookUrl = _configuration["Alerts:AdminWebhookUrl"]?.Trim();
        var configured = !string.IsNullOrWhiteSpace(webhookUrl);
        var status = await _database.EnsureSystemEventDeliveryAsync(
            candidate.EventId,
            configured,
            cancellationToken);
        if (!configured || status is "DELIVERED" or "FAILED")
        {
            return;
        }

        var payload = new
        {
            type = "SCANNER_SYSTEM_EVENT",
            eventId = candidate.EventId,
            candidate.Severity,
            candidate.EventCode,
            candidate.Message,
            candidate.Details,
            candidate.OccurredAtUtc
        };
        try
        {
            var client = _httpClientFactory.CreateClient("alert-webhook");
            using var response = await client.PostAsJsonAsync(webhookUrl, payload, cancellationToken);
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            await _database.CompleteSystemEventDeliveryAsync(
                candidate.EventId,
                response.IsSuccessStatusCode ? "DELIVERED" : "FAILED",
                (int)response.StatusCode,
                Truncate(body),
                cancellationToken);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            await _database.CompleteSystemEventDeliveryAsync(
                candidate.EventId,
                "FAILED",
                null,
                Truncate(exception.Message),
                cancellationToken);
            _logger.LogError(exception, "Admin system-event webhook failed for event {EventId}", candidate.EventId);
        }
    }

    private static string Truncate(string value) =>
        value.Length > 2000 ? value[..2000] : value;
}
