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
                await EvaluateAsync(stoppingToken);
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

    private async Task EvaluateAsync(CancellationToken cancellationToken)
    {
        var vegetableThreshold = _configuration.GetValue("Alerts:VegetableStreakThreshold", 15);
        var meatThreshold = _configuration.GetValue("Alerts:MeatStreakThreshold", 3);
        var webhookUrl = _configuration["Alerts:WebhookUrl"]?.Trim();
        var candidate = await _database.GetCurrentAlertCandidateAsync(
            vegetableThreshold,
            meatThreshold,
            cancellationToken);
        if (candidate is null)
        {
            return;
        }

        var configured = !string.IsNullOrWhiteSpace(webhookUrl);
        var status = await _database.EnsureAlertAsync(candidate, configured, cancellationToken);
        if (!configured || status is "DELIVERED" or "FAILED")
        {
            return;
        }

        try
        {
            var client = _httpClientFactory.CreateClient("alert-webhook");
            using var response = await client.PostAsJsonAsync(
                webhookUrl,
                candidate.Payload,
                cancellationToken);
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            await _database.CompleteAlertAsync(
                candidate.AlertKey,
                response.IsSuccessStatusCode ? "DELIVERED" : "FAILED",
                (int)response.StatusCode,
                body.Length > 2000 ? body[..2000] : body,
                cancellationToken);
            _logger.LogInformation(
                "Webhook {AlertKey} completed with HTTP {StatusCode}",
                candidate.AlertKey,
                (int)response.StatusCode);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            await _database.CompleteAlertAsync(
                candidate.AlertKey,
                "FAILED",
                null,
                exception.Message,
                cancellationToken);
            _logger.LogError(exception, "Webhook {AlertKey} failed", candidate.AlertKey);
        }
    }
}
