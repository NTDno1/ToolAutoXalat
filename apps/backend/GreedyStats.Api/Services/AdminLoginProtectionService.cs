using System.Collections.Concurrent;

namespace GreedyStats.Api.Services;

public sealed record AdminLoginAttempt(bool Allowed, int RetryAfterSeconds);

public sealed class AdminLoginProtectionService
{
    private readonly ConcurrentDictionary<string, AttemptState> _attempts = new();
    private readonly int _maxAttempts;
    private readonly TimeSpan _window;
    private readonly TimeSpan _lockout;

    public AdminLoginProtectionService(IConfiguration configuration)
    {
        _maxAttempts = Math.Clamp(
            configuration.GetValue<int?>("Admin:LoginProtection:MaxAttempts") ?? 5,
            3,
            20);
        _window = TimeSpan.FromMinutes(Math.Clamp(
            configuration.GetValue<int?>("Admin:LoginProtection:WindowMinutes") ?? 15,
            1,
            120));
        _lockout = TimeSpan.FromMinutes(Math.Clamp(
            configuration.GetValue<int?>("Admin:LoginProtection:LockoutMinutes") ?? 15,
            1,
            1440));
    }

    public AdminLoginAttempt Check(string clientIp, string? username)
    {
        var key = BuildKey(clientIp, username);
        var now = DateTimeOffset.UtcNow;
        if (!_attempts.TryGetValue(key, out var state))
        {
            return new AdminLoginAttempt(true, 0);
        }

        lock (state)
        {
            if (state.LockedUntilUtc is { } lockedUntil && lockedUntil > now)
            {
                return new AdminLoginAttempt(
                    false,
                    Math.Max(1, (int)Math.Ceiling((lockedUntil - now).TotalSeconds)));
            }

            if (now - state.WindowStartedUtc >= _window)
            {
                _attempts.TryRemove(key, out _);
                return new AdminLoginAttempt(true, 0);
            }

            return new AdminLoginAttempt(true, 0);
        }
    }

    public AdminLoginAttempt RecordFailure(string clientIp, string? username)
    {
        var key = BuildKey(clientIp, username);
        var now = DateTimeOffset.UtcNow;
        var state = _attempts.GetOrAdd(key, _ => new AttemptState(now));
        lock (state)
        {
            if (now - state.WindowStartedUtc >= _window)
            {
                state.WindowStartedUtc = now;
                state.FailedAttempts = 0;
                state.LockedUntilUtc = null;
            }

            state.FailedAttempts++;
            if (state.FailedAttempts < _maxAttempts)
            {
                return new AdminLoginAttempt(true, 0);
            }

            state.LockedUntilUtc = now + _lockout;
            return new AdminLoginAttempt(false, (int)_lockout.TotalSeconds);
        }
    }

    public void RecordSuccess(string clientIp, string? username) =>
        _attempts.TryRemove(BuildKey(clientIp, username), out _);

    // Limit by IP rather than only IP + username so rotating fake usernames
    // cannot bypass protection for the single Admin login endpoint.
    private static string BuildKey(string clientIp, string? username) =>
        clientIp.Trim().ToLowerInvariant();

    private sealed class AttemptState(DateTimeOffset windowStartedUtc)
    {
        public DateTimeOffset WindowStartedUtc { get; set; } = windowStartedUtc;
        public int FailedAttempts { get; set; }
        public DateTimeOffset? LockedUntilUtc { get; set; }
    }
}
