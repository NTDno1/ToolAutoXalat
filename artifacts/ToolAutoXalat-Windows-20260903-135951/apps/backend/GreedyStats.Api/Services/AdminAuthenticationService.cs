using System.Security.Cryptography;
using System.Text;

namespace GreedyStats.Api.Services;

public sealed class AdminAuthenticationService
{
    private const string HashPrefix = "PBKDF2-SHA256";
    private readonly IConfiguration _configuration;

    public AdminAuthenticationService(IConfiguration configuration)
    {
        _configuration = configuration;
    }

    public string Username => _configuration["Admin:Username"]?.Trim() ?? string.Empty;

    public bool IsConfigured =>
        !string.IsNullOrWhiteSpace(Username) &&
        TryParsePasswordHash(_configuration["Admin:PasswordHash"], out _, out _, out _);

    public int SessionHours => Math.Clamp(
        _configuration.GetValue<int?>("Admin:SessionHours") ?? 8,
        1,
        24);

    public bool Verify(string? username, string? password)
    {
        if (string.IsNullOrWhiteSpace(username) || password is null ||
            !string.Equals(username.Trim(), Username, StringComparison.OrdinalIgnoreCase) ||
            !TryParsePasswordHash(_configuration["Admin:PasswordHash"], out var iterations, out var salt, out var expected))
        {
            return false;
        }

        var actual = Rfc2898DeriveBytes.Pbkdf2(
            Encoding.UTF8.GetBytes(password),
            salt,
            iterations,
            HashAlgorithmName.SHA256,
            expected.Length);

        return CryptographicOperations.FixedTimeEquals(actual, expected);
    }

    private static bool TryParsePasswordHash(
        string? encoded,
        out int iterations,
        out byte[] salt,
        out byte[] hash)
    {
        iterations = 0;
        salt = [];
        hash = [];
        if (string.IsNullOrWhiteSpace(encoded))
        {
            return false;
        }

        var parts = encoded.Split('$', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 4 || !string.Equals(parts[0], HashPrefix, StringComparison.Ordinal) ||
            !int.TryParse(parts[1], out iterations) || iterations < 100_000)
        {
            return false;
        }

        try
        {
            salt = Convert.FromBase64String(parts[2]);
            hash = Convert.FromBase64String(parts[3]);
            return salt.Length >= 16 && hash.Length >= 32;
        }
        catch (FormatException)
        {
            return false;
        }
    }
}
