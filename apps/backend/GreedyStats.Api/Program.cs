using GreedyStats.Api.Data;
using GreedyStats.Api.Models;
using GreedyStats.Api.Services;

var builder = WebApplication.CreateBuilder(args);
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase;
});
builder.Services.AddCors(options => options.AddDefaultPolicy(policy =>
    policy.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));
builder.Services.AddSingleton<GreedyDatabase>();
builder.Services.AddHttpClient("alert-webhook", client =>
{
    client.Timeout = TimeSpan.FromSeconds(10);
});
builder.Services.AddHostedService<AlertMonitorService>();

var app = builder.Build();
app.UseCors();

var database = app.Services.GetRequiredService<GreedyDatabase>();
await database.InitializeAsync();

static bool ValidPagination(int page, int pageSize) =>
    page >= 1 && pageSize is >= 1 and <= 10000;

app.MapGet("/", () => Results.Ok(new
{
    service = "GreedyStats.Api",
    status = "running",
    endpoints = new[]
    {
        "/health",
        "/api/results?page=1&pageSize=50",
        "/api/stats/today",
        "/api/scanner/status",
        "/api/scanner/events",
        "/api/alerts"
    }
}));

app.MapGet("/health", async (GreedyDatabase db, CancellationToken cancellationToken) =>
{
    var scanner = await db.GetScannerStatusAsync(cancellationToken);
    return Results.Ok(new
    {
        status = "ok",
        service = "GreedyStats.Api",
        utcNow = DateTimeOffset.UtcNow,
        scanner
    });
});

app.MapGet("/api/results", async (
    int? page,
    int? pageSize,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    var requestedPage = page ?? 1;
    var requestedSize = pageSize ?? 50;
    if (!ValidPagination(requestedPage, requestedSize))
    {
        return Results.BadRequest(new
        {
            error = "page phải >= 1; pageSize phải nằm trong khoảng 1..10000"
        });
    }
    return Results.Ok(await db.GetResultsAsync(
        requestedPage, requestedSize, cancellationToken));
});

app.MapGet("/api/stats/today", async (
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetTodayStatsAsync(cancellationToken)));

app.MapGet("/api/scanner/status", async (
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetScannerStatusAsync(cancellationToken)));

app.MapGet("/api/scanner/events", async (
    int? page,
    int? pageSize,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    var requestedPage = page ?? 1;
    var requestedSize = pageSize ?? 50;
    if (!ValidPagination(requestedPage, requestedSize))
    {
        return Results.BadRequest(new { error = "Phân trang không hợp lệ" });
    }
    return Results.Ok(await db.GetEventsAsync(
        requestedPage, requestedSize, cancellationToken));
});

app.MapPost("/api/scanner/events", async (
    ScannerEventRequest request,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.EventCode) ||
        string.IsNullOrWhiteSpace(request.Message))
    {
        return Results.BadRequest(new { error = "eventCode và message là bắt buộc" });
    }
    var id = await db.InsertEventAsync(request, cancellationToken);
    return Results.Accepted($"/api/scanner/events/{id}", new { id });
});

app.MapPatch("/api/scanner/events/{id:long}/acknowledge", async (
    long id,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    await db.AcknowledgeEventAsync(id, cancellationToken)
        ? Results.NoContent()
        : Results.NotFound());

app.MapGet("/api/alerts", async (
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetAlertDeliveriesAsync(cancellationToken)));

app.Run();
