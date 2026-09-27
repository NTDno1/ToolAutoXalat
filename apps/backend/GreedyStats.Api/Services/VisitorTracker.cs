using System.Collections.Concurrent;
using GreedyStats.Api.Models;

namespace GreedyStats.Api.Services;

public sealed class VisitorTracker
{
    private readonly ConcurrentDictionary<string, VisitorSnapshot> _visitors = new();

    public void Record(HttpContext context)
    {
        var path = context.Request.Path.Value ?? "/";
        if (path.StartsWith("/assets/", StringComparison.OrdinalIgnoreCase) ||
            path.Equals("/favicon.ico", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        var now = DateTimeOffset.UtcNow;
        var ip = ResolveClientIp(context);
        var userAgent = Header(context, "User-Agent") ?? "Không xác định";
        var deviceId = ClientHeader(context, "X-Device-Id") ?? $"legacy:{ip}:{userAgent}";
        var sessionId = ClientHeader(context, "X-Session-Id") ?? "không có mã phiên";
        var visitorId = $"{deviceId}|{sessionId}";
        var isAdmin = context.User.Identity?.IsAuthenticated == true && context.User.IsInRole("Admin");
        var access = context.Items["AccessSession"] as AccessSessionDto;
        var accessType = isAdmin ? "admin" : access?.AccessType ?? "guest";
        var accountName = isAdmin ? context.User.Identity?.Name : access?.KeyLabel;
        var snapshot = new VisitorSnapshot(
            visitorId,
            deviceId,
            sessionId,
            ClientHeader(context, "X-Device-Name") ?? DescribeDevice(userAgent),
            DescribeDeviceType(userAgent, Header(context, "Sec-CH-UA-Mobile")),
            ClientHeader(context, "X-Client-Platform") ?? Header(context, "Sec-CH-UA-Platform") ?? "Không xác định",
            DescribeBrowser(userAgent, Header(context, "Sec-CH-UA")),
            ip,
            GeoHeader(context, "CF-IPCountry", "X-Vercel-IP-Country"),
            GeoHeader(context, "CF-Region", "X-Vercel-IP-Country-Region"),
            GeoHeader(context, "CF-IPCity", "X-Vercel-IP-City"),
            userAgent,
            ClientHeader(context, "X-Client-Language") ?? Header(context, "Accept-Language"),
            ClientHeader(context, "X-Client-Timezone"),
            ClientHeader(context, "X-Client-Screen"),
            ClientHeader(context, "X-Client-Viewport"),
            ParseDouble(ClientHeader(context, "X-Client-Pixel-Ratio")),
            ParseInt(ClientHeader(context, "X-Client-Touch-Points")),
            ClientHeader(context, "X-Client-Connection"),
            accessType,
            accountName,
            isAdmin,
            isAdmin ? context.User.Identity?.Name : null,
            context.Request.Method,
            context.Request.Path + context.Request.QueryString,
            Header(context, "Referer"),
            context.Request.Host.Value,
            context.Request.Scheme,
            now,
            now,
            1);

        _visitors.AddOrUpdate(
            visitorId,
            _ => snapshot,
            (_, current) => snapshot with
            {
                FirstSeenUtc = current.FirstSeenUtc,
                RequestCount = current.RequestCount + 1
            });
    }

    public IReadOnlyList<VisitorDto> GetVisitors(TimeSpan onlineWindow)
    {
        var now = DateTimeOffset.UtcNow;
        var cutoff = now - TimeSpan.FromHours(24);
        foreach (var item in _visitors)
        {
            if (item.Value.LastSeenUtc < cutoff)
            {
                _visitors.TryRemove(item.Key, out _);
            }
        }

        return _visitors.Values
            .OrderByDescending(item => item.LastSeenUtc)
            .Select(item => new VisitorDto(
                item.VisitorId,
                item.DeviceId,
                item.SessionId,
                item.DeviceName,
                item.DeviceType,
                item.Platform,
                item.Browser,
                item.ClientIp,
                item.Country,
                item.Region,
                item.City,
                item.UserAgent,
                item.BrowserLanguage,
                item.TimeZone,
                item.ScreenSize,
                item.ViewportSize,
                item.PixelRatio,
                item.TouchPoints,
                item.ConnectionType,
                item.AccessType,
                item.AccountName,
                item.IsAdmin,
                item.AdminName,
                item.LastMethod,
                item.LastPath,
                item.Referrer,
                item.Host,
                item.Protocol,
                item.FirstSeenUtc,
                item.LastSeenUtc,
                (now - item.LastSeenUtc) <= onlineWindow,
                item.RequestCount))
            .ToList();
    }

    private static string ResolveClientIp(HttpContext context)
    {
        foreach (var header in new[] { "CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For" })
        {
            var value = Header(context, header);
            if (string.IsNullOrWhiteSpace(value)) continue;
            var first = value.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
            if (!string.IsNullOrWhiteSpace(first)) return first;
        }
        return context.Connection.RemoteIpAddress?.ToString() ?? "Không xác định";
    }

    private static string? Header(HttpContext context, string name)
    {
        var value = context.Request.Headers[name].ToString().Trim();
        return string.IsNullOrWhiteSpace(value) ? null : Truncate(value, 600);
    }

    private static string? ClientHeader(HttpContext context, string name)
    {
        var value = Header(context, name);
        if (value is null) return null;
        try
        {
            return Uri.UnescapeDataString(value);
        }
        catch (UriFormatException)
        {
            return value;
        }
    }

    private static string? GeoHeader(HttpContext context, params string[] names) =>
        names.Select(name => Header(context, name)).FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));

    private static string DescribeDevice(string userAgent) =>
        $"{DescribeDeviceType(userAgent, null)} · {DescribeBrowser(userAgent, null)}";

    private static string DescribeDeviceType(string userAgent, string? mobileHint)
    {
        if (mobileHint == "?1" || userAgent.Contains("Mobile", StringComparison.OrdinalIgnoreCase)) return "Điện thoại";
        if (userAgent.Contains("iPad", StringComparison.OrdinalIgnoreCase) || userAgent.Contains("Tablet", StringComparison.OrdinalIgnoreCase)) return "Máy tính bảng";
        return "Máy tính";
    }

    private static string DescribeBrowser(string userAgent, string? clientHint)
    {
        if (!string.IsNullOrWhiteSpace(clientHint)) return clientHint.Replace('"', ' ').Trim();
        if (userAgent.Contains("Edg/", StringComparison.OrdinalIgnoreCase)) return "Microsoft Edge";
        if (userAgent.Contains("OPR/", StringComparison.OrdinalIgnoreCase)) return "Opera";
        if (userAgent.Contains("Firefox/", StringComparison.OrdinalIgnoreCase)) return "Firefox";
        if (userAgent.Contains("Chrome/", StringComparison.OrdinalIgnoreCase)) return "Chrome";
        if (userAgent.Contains("Safari/", StringComparison.OrdinalIgnoreCase)) return "Safari";
        return "Trình duyệt không xác định";
    }

    private static double? ParseDouble(string? value) =>
        double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var parsed)
            ? parsed
            : null;

    private static int? ParseInt(string? value) => int.TryParse(value, out var parsed) ? parsed : null;

    private static string Truncate(string value, int maximum) =>
        value.Length <= maximum ? value : value[..maximum];

    private sealed record VisitorSnapshot(
        string VisitorId,
        string DeviceId,
        string SessionId,
        string DeviceName,
        string DeviceType,
        string Platform,
        string Browser,
        string ClientIp,
        string? Country,
        string? Region,
        string? City,
        string UserAgent,
        string? BrowserLanguage,
        string? TimeZone,
        string? ScreenSize,
        string? ViewportSize,
        double? PixelRatio,
        int? TouchPoints,
        string? ConnectionType,
        string AccessType,
        string? AccountName,
        bool IsAdmin,
        string? AdminName,
        string LastMethod,
        string LastPath,
        string? Referrer,
        string? Host,
        string? Protocol,
        DateTimeOffset FirstSeenUtc,
        DateTimeOffset LastSeenUtc,
        long RequestCount);
}
