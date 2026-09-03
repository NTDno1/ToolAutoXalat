using System.Globalization;
using System.Security.Claims;
using System.Text.RegularExpressions;
using GreedyStats.Api.Data;
using GreedyStats.Api.Models;
using GreedyStats.Api.Services;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.ResponseCompression;

var builder = WebApplication.CreateBuilder(args);
builder.Configuration.AddJsonFile("appsettings.Admin.json", optional: true, reloadOnChange: true);
builder.Configuration.AddJsonFile("appsettings.Secrets.json", optional: true, reloadOnChange: true);
builder.Configuration.AddEnvironmentVariables();
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase;
});
builder.Services.AddCors(options => options.AddDefaultPolicy(policy =>
    policy.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));
builder.Services.AddResponseCompression(options =>
{
    options.EnableForHttps = true;
    options.Providers.Add<BrotliCompressionProvider>();
    options.Providers.Add<GzipCompressionProvider>();
});
builder.Services.AddSingleton<GreedyDatabase>();
builder.Services.AddSingleton<PredictionService>();
builder.Services.AddSingleton<AdminAuthenticationService>();
builder.Services.AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.Cookie.Name = "greedy_admin";
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
        options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
        options.ExpireTimeSpan = TimeSpan.FromHours(8);
        options.SlidingExpiration = true;
        options.Events.OnRedirectToLogin = context =>
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            return Task.CompletedTask;
        };
        options.Events.OnRedirectToAccessDenied = context =>
        {
            context.Response.StatusCode = StatusCodes.Status403Forbidden;
            return Task.CompletedTask;
        };
    });
builder.Services.AddAuthorization();
builder.Services.AddHttpClient("alert-webhook", client =>
{
    client.Timeout = TimeSpan.FromSeconds(10);
});
builder.Services.AddHttpClient("ai-prediction-provider", client =>
{
    client.Timeout = TimeSpan.FromSeconds(20);
});
builder.Services.AddHostedService<AlertMonitorService>();

var app = builder.Build();
app.UseResponseCompression();
app.UseCors();
app.UseAuthentication();
app.UseAuthorization();

var database = app.Services.GetRequiredService<GreedyDatabase>();
await database.InitializeAsync();

static bool ValidPagination(int page, int pageSize) =>
    page >= 1 && pageSize is >= 1 and <= 10000;

static bool TryResolveDate(string? raw, GreedyDatabase db, out DateOnly date)
{
    if (string.IsNullOrWhiteSpace(raw))
    {
        date = db.LocalToday;
        return true;
    }
    return DateOnly.TryParseExact(
        raw,
        "yyyy-MM-dd",
        CultureInfo.InvariantCulture,
        DateTimeStyles.None,
        out date);
}

static string? NormalizeVietnamPhone(string? raw)
{
    if (string.IsNullOrWhiteSpace(raw))
    {
        return null;
    }
    var compact = Regex.Replace(raw.Trim(), "[\\s.()-]", string.Empty);
    if (Regex.IsMatch(compact, "^0[0-9]{9}$"))
    {
        return "+84" + compact[1..];
    }
    return Regex.IsMatch(compact, "^\\+84[0-9]{9}$") ? compact : null;
}

app.MapGet("/", () => Results.Ok(new
{
    service = "GreedyStats.Api",
    status = "running",
    version = "2.0",
    endpoints = new[]
    {
        "/health",
        "/api/results?page=1&pageSize=50&date=yyyy-MM-dd",
        "/api/stats/today",
        "/api/stats/daily?date=yyyy-MM-dd",
        "/api/stats/streaks?date=yyyy-MM-dd&category=VEGETABLE&length=2",
        "/api/stats/days",
        "/api/predictions/next?date=yyyy-MM-dd",
        "/api/predictions/ai?date=yyyy-MM-dd",
        "/api/predictions/compound?date=yyyy-MM-dd",
        "/api/predictions/context?date=yyyy-MM-dd",
        "/api/subscribers",
        "/api/auth/session",
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

app.MapGet("/api/auth/session", (
    HttpContext context,
    AdminAuthenticationService adminAuth) =>
    Results.Ok(new AdminSessionDto(
        context.User.Identity?.IsAuthenticated == true && context.User.IsInRole("Admin"),
        adminAuth.IsConfigured,
        context.User.Identity?.IsAuthenticated == true ? context.User.Identity.Name : null)));

app.MapPost("/api/auth/login", async (
    AdminLoginRequest request,
    HttpContext context,
    AdminAuthenticationService adminAuth) =>
{
    if (!adminAuth.IsConfigured)
    {
        return Results.Json(
            new { error = "Tài khoản Admin chưa được cấu hình trên backend." },
            statusCode: StatusCodes.Status503ServiceUnavailable);
    }

    if (!adminAuth.Verify(request.Username, request.Password))
    {
        await Task.Delay(250);
        return Results.Json(
            new { error = "Tên đăng nhập hoặc mật khẩu không đúng." },
            statusCode: StatusCodes.Status401Unauthorized);
    }

    var claims = new[]
    {
        new Claim(ClaimTypes.Name, adminAuth.Username),
        new Claim(ClaimTypes.Role, "Admin")
    };
    var identity = new ClaimsIdentity(claims, CookieAuthenticationDefaults.AuthenticationScheme);
    await context.SignInAsync(
        CookieAuthenticationDefaults.AuthenticationScheme,
        new ClaimsPrincipal(identity),
        new AuthenticationProperties
        {
            IsPersistent = false,
            AllowRefresh = true,
            ExpiresUtc = DateTimeOffset.UtcNow.AddHours(adminAuth.SessionHours)
        });

    return Results.Ok(new AdminSessionDto(true, true, adminAuth.Username));
});

app.MapPost("/api/auth/logout", async (HttpContext context) =>
{
    await context.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
    return Results.NoContent();
}).RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapGet("/api/results", async (
    int? page,
    int? pageSize,
    string? date,
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
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await db.GetResultsAsync(
        requestedPage, requestedSize, selectedDate, cancellationToken));
});

app.MapGet("/api/stats/today", async (
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetTodayStatsAsync(cancellationToken)));

app.MapGet("/api/stats/daily", async (
    string? date,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await db.GetStatsForDateAsync(selectedDate, cancellationToken));
});

app.MapGet("/api/stats/daily/compact", async (
    string? date,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await db.GetCompactStatsForDateAsync(selectedDate, cancellationToken));
});

app.MapGet("/api/stats/streaks", async (
    string? date,
    string? category,
    int? length,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }

    var normalizedCategory = category?.Trim().ToUpperInvariant();
    if (normalizedCategory is not ("VEGETABLE" or "MEAT"))
    {
        return Results.BadRequest(new { error = "category phải là VEGETABLE hoặc MEAT" });
    }
    if (length is null or < 1)
    {
        return Results.BadRequest(new { error = "length phải >= 1" });
    }

    var stats = await db.GetStatsForDateAsync(selectedDate, cancellationToken);
    var runs = normalizedCategory == "VEGETABLE"
        ? stats.VegetableRuns
        : stats.MeatRuns;
    var matchingRuns = runs.Where(item => item.Length == length.Value).ToList();
    return Results.Ok(new StreakBucketDto(length.Value, matchingRuns.Count, matchingRuns));
});

app.MapGet("/api/stats/days", async (
    int? limit,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetDailySummariesAsync(
        Math.Clamp(limit ?? 60, 1, 366), cancellationToken)));

app.MapGet("/api/predictions/next", async (
    string? date,
    GreedyDatabase db,
    PredictionService predictions,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await predictions.GetPredictionAsync(selectedDate, cancellationToken));
});

app.MapGet("/api/predictions/ai", async (
    string? date,
    bool? force,
    GreedyDatabase db,
    PredictionService predictions,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await predictions.GetAiPredictionAsync(
        selectedDate,
        force ?? false,
        cancellationToken));
});

app.MapGet("/api/predictions/compound", async (
    string? date,
    bool? force,
    GreedyDatabase db,
    PredictionService predictions,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await predictions.GetCompoundPredictionAsync(
        selectedDate,
        force ?? false,
        cancellationToken));
});

app.MapGet("/api/predictions/context", async (
    string? date,
    GreedyDatabase db,
    PredictionService predictions,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await predictions.GetContextAsync(selectedDate, cancellationToken));
});

app.MapGet("/api/payment/config", (IConfiguration configuration) =>
{
    var qrImageUrl = configuration["Payment:QrImageUrl"]?.Trim() ?? string.Empty;
    return Results.Ok(new PaymentConfigDto(
        !string.IsNullOrWhiteSpace(qrImageUrl),
        qrImageUrl,
        Math.Max(0, configuration.GetValue<decimal>("Payment:Amount")),
        configuration["Payment:Currency"]?.Trim() ?? "VND",
        configuration["Payment:BankName"]?.Trim() ?? string.Empty,
        configuration["Payment:AccountName"]?.Trim() ?? string.Empty,
        configuration["Payment:AccountNumber"]?.Trim() ?? string.Empty,
        configuration["Payment:TransferPrefix"]?.Trim() ?? "DK",
        configuration["Payment:Instructions"]?.Trim() ??
            "Sau khi thanh toán, quản trị viên sẽ đối soát và thêm số điện thoại vào webhook chung."));
});

app.MapGet("/api/subscribers", async (
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetSubscribersAsync(false, cancellationToken)));

app.MapPost("/api/subscribers", async (
    SubscriberRequest request,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    var phone = NormalizeVietnamPhone(request.PhoneNumber);
    if (phone is null)
    {
        return Results.BadRequest(new
        {
            error = "Số điện thoại Việt Nam không hợp lệ. Ví dụ: 0912345678 hoặc +84912345678"
        });
    }
    var subscriber = await db.UpsertSubscriberAsync(
        phone,
        string.IsNullOrWhiteSpace(request.DisplayName) ? null : request.DisplayName.Trim(),
        cancellationToken);
    return Results.Ok(subscriber);
});

app.MapDelete("/api/subscribers/{id:long}", async (
    long id,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    await db.DeactivateSubscriberAsync(id, cancellationToken)
        ? Results.NoContent()
        : Results.NotFound());

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
}).RequireAuthorization(policy => policy.RequireRole("Admin"));

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
        : Results.NotFound())
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapGet("/api/alerts", async (
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetAlertDeliveriesAsync(cancellationToken)))
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.Run();
