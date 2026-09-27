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
builder.Services.AddSingleton<AdminLoginProtectionService>();
builder.Services.AddSingleton<AccessKeyService>();
builder.Services.AddSingleton<PhoneControlService>();
builder.Services.AddSingleton<AutoPlayService>();
builder.Services.AddSingleton<VisitorTracker>();
builder.Services.AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.Cookie.Name = "greedy_admin";
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
        options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
        options.ExpireTimeSpan = TimeSpan.FromHours(8);
        options.SlidingExpiration = true;
        options.Events.OnValidatePrincipal = async context =>
        {
            var adminAuth = context.HttpContext.RequestServices
                .GetRequiredService<AdminAuthenticationService>();
            var credentialVersion = context.Principal?.FindFirstValue(
                AdminAuthenticationService.CredentialVersionClaim);
            var username = context.Principal?.Identity?.Name;
            if (!adminAuth.IsConfigured ||
                !string.Equals(username, adminAuth.Username, StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(
                    credentialVersion,
                    adminAuth.CredentialVersion,
                    StringComparison.Ordinal))
            {
                context.RejectPrincipal();
                await context.HttpContext.SignOutAsync(
                    CookieAuthenticationDefaults.AuthenticationScheme);
            }
        };
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
builder.Services.AddHostedService(provider => provider.GetRequiredService<AutoPlayService>());

var app = builder.Build();
app.UseResponseCompression();
app.UseCors();
app.UseAuthentication();
app.Use(async (context, next) =>
{
    var path = context.Request.Path;
    var isPublicApi = path.StartsWithSegments("/api/auth") ||
        path.StartsWithSegments("/api/access") ||
        (HttpMethods.IsPost(context.Request.Method) && path.Equals("/api/scanner/events"));
    var isAdmin = context.User.Identity?.IsAuthenticated == true && context.User.IsInRole("Admin");

    if (!path.StartsWithSegments("/api") || isPublicApi || isAdmin)
    {
        await next();
        return;
    }

    var accessKeys = context.RequestServices.GetRequiredService<AccessKeyService>();
    var session = await accessKeys.ValidateSessionAsync(
        context.Request.Cookies[AccessKeyService.CookieName],
        ResolveClientIp(context),
        context.Request.Headers.UserAgent.ToString(),
        context.RequestAborted);
    if (!session.IsAuthorized)
    {
        context.Response.Cookies.Delete(AccessKeyService.CookieName);
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        await context.Response.WriteAsJsonAsync(new
        {
            error = session.KeyExpired
                ? "Key đã hết hạn. Vui lòng liên hệ quản trị viên để được cấp key mới."
                : "Bạn cần nhập key hợp lệ để xem nội dung.",
            code = session.KeyExpired ? "ACCESS_KEY_EXPIRED" : "ACCESS_KEY_REQUIRED"
        });
        return;
    }

    context.Items["AccessSession"] = session;
    await next();
});
app.UseAuthorization();
app.Use(async (context, next) =>
{
    var visitors = context.RequestServices.GetRequiredService<VisitorTracker>();
    visitors.Record(context);
    await next();
});

var database = app.Services.GetRequiredService<GreedyDatabase>();
await database.InitializeAsync();
var accessKeyService = app.Services.GetRequiredService<AccessKeyService>();
await accessKeyService.InitializeAsync();

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

static string ResolveClientIp(HttpContext context)
{
    var cloudflareIp = context.Request.Headers["CF-Connecting-IP"].FirstOrDefault();
    if (!string.IsNullOrWhiteSpace(cloudflareIp)) return cloudflareIp.Trim();
    var realIp = context.Request.Headers["X-Real-IP"].FirstOrDefault();
    if (!string.IsNullOrWhiteSpace(realIp)) return realIp.Trim();
    var forwarded = context.Request.Headers["X-Forwarded-For"].FirstOrDefault();
    if (!string.IsNullOrWhiteSpace(forwarded)) return forwarded.Split(',')[0].Trim();
    return context.Connection.RemoteIpAddress?.ToString() ?? "unknown";
}

static bool CanUsePhoneControl(
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration)
{
    var requireAdmin = configuration.GetValue("PhoneControl:RequireAdmin", adminAuth.IsConfigured);
    return !requireAdmin ||
        (context.User.Identity?.IsAuthenticated == true && context.User.IsInRole("Admin"));
}

static IResult PhoneControlForbidden() =>
    Results.Json(new { error = "Phone preview requires admin login." }, statusCode: StatusCodes.Status403Forbidden);

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
        "/api/predictions/market?date=yyyy-MM-dd",
        "/api/predictions/ai?date=yyyy-MM-dd",
        "/api/predictions/compound?date=yyyy-MM-dd",
        "/api/predictions/context?date=yyyy-MM-dd",
        "/api/subscribers",
        "/api/auth/session",
        "/api/admin/visitors",
        "/api/scanner/status",
        "/api/scanner/events",
        "/api/phone/control/status",
        "/api/phone/screenshot",
        "/api/admin/autoplay/status",
        "/api/alerts",
        "/api/alerts/webhook/config"
    }
}));

app.MapGet("/health", () =>
{
    return Results.Ok(new
    {
        status = "ok",
        service = "GreedyStats.Api",
        utcNow = DateTimeOffset.UtcNow
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
    AdminAuthenticationService adminAuth,
    AdminLoginProtectionService loginProtection) =>
{
    if (!adminAuth.IsConfigured)
    {
        return Results.Json(
            new { error = "Tài khoản Admin chưa được cấu hình trên backend." },
            statusCode: StatusCodes.Status503ServiceUnavailable);
    }

    var clientIp = ResolveClientIp(context);
    var attempt = loginProtection.Check(clientIp, request.Username);
    if (!attempt.Allowed)
    {
        context.Response.Headers.RetryAfter = attempt.RetryAfterSeconds.ToString(CultureInfo.InvariantCulture);
        return Results.Json(
            new
            {
                error = $"Đăng nhập tạm khóa do thử sai quá nhiều lần. Vui lòng thử lại sau {Math.Max(1, (int)Math.Ceiling(attempt.RetryAfterSeconds / 60d))} phút.",
                code = "ADMIN_LOGIN_LOCKED",
                retryAfterSeconds = attempt.RetryAfterSeconds
            },
            statusCode: StatusCodes.Status429TooManyRequests);
    }

    if (!adminAuth.Verify(request.Username, request.Password))
    {
        var failure = loginProtection.RecordFailure(clientIp, request.Username);
        await Task.Delay(350);
        if (!failure.Allowed)
        {
            context.Response.Headers.RetryAfter = failure.RetryAfterSeconds.ToString(CultureInfo.InvariantCulture);
            return Results.Json(
                new
                {
                    error = $"Đăng nhập tạm khóa do thử sai quá nhiều lần. Vui lòng thử lại sau {Math.Max(1, (int)Math.Ceiling(failure.RetryAfterSeconds / 60d))} phút.",
                    code = "ADMIN_LOGIN_LOCKED",
                    retryAfterSeconds = failure.RetryAfterSeconds
                },
                statusCode: StatusCodes.Status429TooManyRequests);
        }
        return Results.Json(
            new { error = "Tên đăng nhập hoặc mật khẩu không đúng." },
            statusCode: StatusCodes.Status401Unauthorized);
    }

    loginProtection.RecordSuccess(clientIp, request.Username);

    var claims = new[]
    {
        new Claim(ClaimTypes.Name, adminAuth.Username),
        new Claim(ClaimTypes.Role, "Admin"),
        new Claim(
            AdminAuthenticationService.CredentialVersionClaim,
            adminAuth.CredentialVersion)
    };
    var identity = new ClaimsIdentity(claims, CookieAuthenticationDefaults.AuthenticationScheme);
    await context.SignInAsync(
        CookieAuthenticationDefaults.AuthenticationScheme,
        new ClaimsPrincipal(identity),
        new AuthenticationProperties
        {
            IsPersistent = request.RememberMe,
            AllowRefresh = true,
            ExpiresUtc = request.RememberMe
                ? DateTimeOffset.UtcNow.AddDays(adminAuth.RememberSessionDays)
                : DateTimeOffset.UtcNow.AddHours(adminAuth.SessionHours)
        });

    return Results.Ok(new AdminSessionDto(true, true, adminAuth.Username));
});

app.MapPost("/api/auth/logout", async (HttpContext context) =>
{
    await context.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
    return Results.NoContent();
}).RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapGet("/api/access/session", async (
    HttpContext context,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
{
    if (context.User.Identity?.IsAuthenticated == true && context.User.IsInRole("Admin"))
    {
        return Results.Ok(new AccessSessionDto(
            true, "admin", null, null, false, accessKeys.DeviceLeaseSeconds));
    }

    var session = await accessKeys.ValidateSessionAsync(
        context.Request.Cookies[AccessKeyService.CookieName],
        ResolveClientIp(context),
        context.Request.Headers.UserAgent.ToString(),
        cancellationToken);
    if (!session.IsAuthorized) context.Response.Cookies.Delete(AccessKeyService.CookieName);
    return Results.Ok(session);
});

app.MapPost("/api/access/login", async Task<IResult> (
    AccessKeyLoginRequest request,
    HttpContext context,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
{
    var result = await accessKeys.LoginAsync(
        request.Key,
        request.DeviceId,
        request.DeviceName,
        ResolveClientIp(context),
        context.Request.Headers.UserAgent.ToString(),
        cancellationToken);
    if (result.Status != AccessKeyLoginStatus.Success || result.SessionToken is null)
    {
        var (status, code, message) = result.Status switch
        {
            AccessKeyLoginStatus.Expired => (
                StatusCodes.Status403Forbidden,
                "ACCESS_KEY_EXPIRED",
                "Key đã hết hạn. Vui lòng liên hệ quản trị viên để được gia hạn."),
            AccessKeyLoginStatus.InUse => (
                StatusCodes.Status409Conflict,
                "ACCESS_KEY_IN_USE",
                $"Key đang được dùng trên thiết bị khác. Hãy đăng xuất thiết bị đó hoặc chờ khoảng {accessKeys.DeviceLeaseSeconds} giây."),
            _ => (
                StatusCodes.Status401Unauthorized,
                "ACCESS_KEY_INVALID",
                "Key không đúng hoặc đã bị khóa.")
        };
        await Task.Delay(200, cancellationToken);
        return Results.Json(new { error = message, code }, statusCode: status);
    }

    context.Response.Cookies.Append(
        AccessKeyService.CookieName,
        result.SessionToken,
        new CookieOptions
        {
            HttpOnly = true,
            SameSite = SameSiteMode.Lax,
            Secure = context.Request.IsHttps,
            Expires = request.RememberMe ? result.Session.KeyExpiresAtUtc : null,
            Path = "/",
            IsEssential = true
        });
    return Results.Ok(result.Session);
});

app.MapPost("/api/access/logout", async (
    HttpContext context,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
{
    await accessKeys.LogoutAsync(
        context.Request.Cookies[AccessKeyService.CookieName],
        cancellationToken);
    context.Response.Cookies.Delete(AccessKeyService.CookieName);
    return Results.NoContent();
});

app.MapGet("/api/admin/access-keys", async (
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
    Results.Ok(await accessKeys.GetKeysAsync(cancellationToken)))
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapPost("/api/admin/access-keys", async Task<IResult> (
    CreateAccessKeyRequest request,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
{
    if (request.ValidDays is < 1 or > 3650)
    {
        return Results.BadRequest(new { error = "Số ngày hết hạn phải từ 1 đến 3650." });
    }
    return Results.Ok(await accessKeys.CreateKeyAsync(request, cancellationToken));
})
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapPut("/api/admin/access-keys/{id:long}/active", async (
    long id,
    SetAccessKeyActiveRequest request,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
    await accessKeys.SetKeyActiveAsync(id, request.IsActive, cancellationToken)
        ? Results.NoContent()
        : Results.NotFound())
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapPost("/api/admin/access-keys/{id:long}/disconnect", async (
    long id,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
{
    await accessKeys.DisconnectKeyAsync(id, cancellationToken);
    return Results.NoContent();
})
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapDelete("/api/admin/access-keys/{id:long}", async (
    long id,
    AccessKeyService accessKeys,
    CancellationToken cancellationToken) =>
    await accessKeys.DeleteKeyAsync(id, cancellationToken)
        ? Results.NoContent()
        : Results.NotFound())
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapGet("/api/admin/visitors", (
    VisitorTracker visitors,
    int? onlineSeconds) =>
    Results.Ok(visitors.GetVisitors(TimeSpan.FromSeconds(Math.Clamp(onlineSeconds ?? 60, 10, 3600)))))
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

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

app.MapGet("/api/predictions/market", async (
    string? date,
    GreedyDatabase db,
    PredictionService predictions,
    CancellationToken cancellationToken) =>
{
    if (!TryResolveDate(date, db, out var selectedDate))
    {
        return Results.BadRequest(new { error = "date phải có định dạng yyyy-MM-dd" });
    }
    return Results.Ok(await predictions.GetMarketPredictionAsync(selectedDate, cancellationToken));
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

app.MapGet("/api/phone/control/status", async (
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration,
    PhoneControlService phoneControl,
    CancellationToken cancellationToken) =>
{
    if (!CanUsePhoneControl(context, adminAuth, configuration)) return PhoneControlForbidden();
    try
    {
        return Results.Ok(await phoneControl.GetStatusAsync(cancellationToken));
    }
    catch (Exception ex)
    {
        return Results.Ok(new PhoneControlStatusDto(
            true,
            false,
            null,
            null,
            null,
            null,
            ex.Message));
    }
});

app.MapGet("/api/phone/screenshot", async (
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration,
    PhoneControlService phoneControl,
    CancellationToken cancellationToken) =>
{
    if (!CanUsePhoneControl(context, adminAuth, configuration)) return PhoneControlForbidden();
    try
    {
        var image = await phoneControl.CaptureImageAsync(cancellationToken);
        return Results.File(image.Bytes, image.ContentType);
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

app.MapPost("/api/phone/preview/stop", (
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration,
    PhoneControlService phoneControl) =>
{
    if (!CanUsePhoneControl(context, adminAuth, configuration)) return PhoneControlForbidden();
    phoneControl.StopPreview();
    return Results.NoContent();
});

app.MapPost("/api/phone/tap", async (
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration,
    PhoneTapRequest request,
    PhoneControlService phoneControl,
    CancellationToken cancellationToken) =>
{
    if (!CanUsePhoneControl(context, adminAuth, configuration)) return PhoneControlForbidden();
    try
    {
        await phoneControl.TapAsync(request, cancellationToken);
        return Results.NoContent();
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

app.MapPost("/api/phone/swipe", async (
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration,
    PhoneSwipeRequest request,
    PhoneControlService phoneControl,
    CancellationToken cancellationToken) =>
{
    if (!CanUsePhoneControl(context, adminAuth, configuration)) return PhoneControlForbidden();
    try
    {
        await phoneControl.SwipeAsync(request, cancellationToken);
        return Results.NoContent();
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

app.MapPost("/api/phone/key", async (
    HttpContext context,
    AdminAuthenticationService adminAuth,
    IConfiguration configuration,
    PhoneKeyRequest request,
    PhoneControlService phoneControl,
    CancellationToken cancellationToken) =>
{
    if (!CanUsePhoneControl(context, adminAuth, configuration)) return PhoneControlForbidden();
    try
    {
        await phoneControl.KeyAsync(request, cancellationToken);
        return Results.NoContent();
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

app.MapGet("/api/admin/autoplay/status", async (
    AutoPlayService autoPlay,
    CancellationToken cancellationToken) =>
    Results.Ok(await autoPlay.GetStatusAsync(cancellationToken)))
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapPut("/api/admin/autoplay/config", async (
    AutoPlayConfigurationRequest request,
    AutoPlayService autoPlay,
    CancellationToken cancellationToken) =>
{
    try
    {
        return Results.Ok(await autoPlay.SaveConfigurationAsync(request, cancellationToken));
    }
    catch (InvalidOperationException ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
}).RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapPost("/api/admin/autoplay/emergency-stop", async (
    AutoPlayService autoPlay,
    CancellationToken cancellationToken) =>
    Results.Ok(await autoPlay.EmergencyStopAsync(cancellationToken)))
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

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

app.MapGet("/api/alerts/webhook/config", async (
    GreedyDatabase db,
    IConfiguration configuration,
    CancellationToken cancellationToken) =>
    Results.Ok(await db.GetAlertWebhookConfigAsync(configuration, cancellationToken)))
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.MapPut("/api/alerts/webhook/config", async Task<IResult> (
    AlertWebhookConfigRequest request,
    GreedyDatabase db,
    CancellationToken cancellationToken) =>
{
    if (request.Enabled && string.IsNullOrWhiteSpace(request.WebhookUrl))
    {
        return Results.BadRequest(new { error = "Nhập URL webhook trước khi bật thông báo." });
    }
    return Results.Ok(await db.SetAlertWebhookConfigAsync(request, cancellationToken));
})
    .RequireAuthorization(policy => policy.RequireRole("Admin"));

app.Run();
