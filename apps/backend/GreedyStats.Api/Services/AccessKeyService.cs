using System.Security.Cryptography;
using System.Text;
using GreedyStats.Api.Models;
using Microsoft.Data.Sqlite;

namespace GreedyStats.Api.Services;

public enum AccessKeyLoginStatus
{
    Success,
    Invalid,
    Expired,
    InUse
}

public sealed record AccessKeyLoginResult(
    AccessKeyLoginStatus Status,
    string? SessionToken,
    AccessSessionDto Session);

public sealed class AccessKeyService
{
    public const string CookieName = "greedy_access";

    private const string Schema = """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA busy_timeout=10000;

        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            key_hint TEXT NOT NULL,
            label TEXT NOT NULL,
            expires_at_utc TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS access_key_sessions (
            access_key_id INTEGER PRIMARY KEY,
            session_hash TEXT NOT NULL UNIQUE,
            device_hash TEXT NOT NULL,
            device_name TEXT,
            client_ip TEXT,
            user_agent TEXT,
            first_seen_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            FOREIGN KEY(access_key_id) REFERENCES access_keys(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS ix_access_keys_active_expiry
            ON access_keys(is_active, expires_at_utc);
        CREATE INDEX IF NOT EXISTS ix_access_sessions_last_seen
            ON access_key_sessions(last_seen_utc DESC);
        """;

    private readonly string _connectionString;
    private readonly int _deviceLeaseSeconds;
    private readonly SemaphoreSlim _writeLock = new(1, 1);

    public AccessKeyService(IConfiguration configuration, IWebHostEnvironment environment)
    {
        var configuredPath = configuration["Database:Path"] ?? "../../../data/greedy_stats.db";
        var fullPath = Path.GetFullPath(configuredPath, environment.ContentRootPath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = fullPath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
            ForeignKeys = true
        }.ToString();
        _deviceLeaseSeconds = Math.Clamp(
            configuration.GetValue<int?>("AccessKeys:DeviceLeaseSeconds") ?? 90,
            30,
            3600);
    }

    public int DeviceLeaseSeconds => _deviceLeaseSeconds;

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = Schema;
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<AccessSessionDto> ValidateSessionAsync(
        string? sessionToken,
        string? clientIp,
        string? userAgent,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(sessionToken))
        {
            return AccessSessionDto.Denied(_deviceLeaseSeconds);
        }

        var now = DateTimeOffset.UtcNow;
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT k.id, k.label, k.expires_at_utc, k.is_active, s.last_seen_utc
            FROM access_key_sessions s
            JOIN access_keys k ON k.id = s.access_key_id
            WHERE s.session_hash = $session_hash
            LIMIT 1
            """;
        command.Parameters.AddWithValue("$session_hash", Hash(sessionToken));
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken))
        {
            return AccessSessionDto.Denied(_deviceLeaseSeconds);
        }

        var keyId = reader.GetInt64(0);
        var label = reader.GetString(1);
        var expiresAt = ParseTimestamp(reader.GetString(2));
        var isActive = reader.GetInt64(3) == 1;
        var lastSeen = ParseTimestamp(reader.GetString(4));
        await reader.DisposeAsync();

        if (!isActive || expiresAt <= now)
        {
            await DeleteSessionAsync(connection, keyId, cancellationToken);
            return AccessSessionDto.Denied(_deviceLeaseSeconds, expiresAt <= now);
        }

        if ((now - lastSeen).TotalSeconds >= 10)
        {
            await using var update = connection.CreateCommand();
            update.CommandText = """
                UPDATE access_key_sessions
                SET last_seen_utc = $now, client_ip = $client_ip, user_agent = $user_agent
                WHERE access_key_id = $key_id AND session_hash = $session_hash
                """;
            update.Parameters.AddWithValue("$now", FormatTimestamp(now));
            update.Parameters.AddWithValue("$client_ip", (object?)clientIp ?? DBNull.Value);
            update.Parameters.AddWithValue("$user_agent", (object?)Truncate(userAgent, 500) ?? DBNull.Value);
            update.Parameters.AddWithValue("$key_id", keyId);
            update.Parameters.AddWithValue("$session_hash", Hash(sessionToken));
            await update.ExecuteNonQueryAsync(cancellationToken);
        }

        return new AccessSessionDto(true, "key", label, expiresAt, false, _deviceLeaseSeconds);
    }

    public async Task<AccessKeyLoginResult> LoginAsync(
        string? rawKey,
        string? deviceId,
        string? deviceName,
        string? clientIp,
        string? userAgent,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(rawKey) || !ValidDeviceId(deviceId))
        {
            return Failed(AccessKeyLoginStatus.Invalid);
        }

        await _writeLock.WaitAsync(cancellationToken);
        try
        {
            var now = DateTimeOffset.UtcNow;
            await using var connection = await OpenAsync(cancellationToken);
            await using var keyCommand = connection.CreateCommand();
            keyCommand.CommandText = """
                SELECT id, label, expires_at_utc, is_active
                FROM access_keys
                WHERE key_hash = $key_hash
                LIMIT 1
                """;
            keyCommand.Parameters.AddWithValue("$key_hash", Hash(NormalizeKey(rawKey)));
            await using var keyReader = await keyCommand.ExecuteReaderAsync(cancellationToken);
            if (!await keyReader.ReadAsync(cancellationToken))
            {
                return Failed(AccessKeyLoginStatus.Invalid);
            }

            var keyId = keyReader.GetInt64(0);
            var label = keyReader.GetString(1);
            var expiresAt = ParseTimestamp(keyReader.GetString(2));
            var isActive = keyReader.GetInt64(3) == 1;
            await keyReader.DisposeAsync();

            if (!isActive) return Failed(AccessKeyLoginStatus.Invalid);
            if (expiresAt <= now) return Failed(AccessKeyLoginStatus.Expired, expired: true);

            var deviceHash = Hash(deviceId!.Trim());
            await using var activeCommand = connection.CreateCommand();
            activeCommand.CommandText = """
                SELECT device_hash, last_seen_utc
                FROM access_key_sessions
                WHERE access_key_id = $key_id
                LIMIT 1
                """;
            activeCommand.Parameters.AddWithValue("$key_id", keyId);
            await using var activeReader = await activeCommand.ExecuteReaderAsync(cancellationToken);
            if (await activeReader.ReadAsync(cancellationToken))
            {
                var activeDeviceHash = activeReader.GetString(0);
                var activeLastSeen = ParseTimestamp(activeReader.GetString(1));
                if (!CryptographicOperations.FixedTimeEquals(
                        Encoding.UTF8.GetBytes(activeDeviceHash),
                        Encoding.UTF8.GetBytes(deviceHash)) &&
                    (now - activeLastSeen).TotalSeconds < _deviceLeaseSeconds)
                {
                    return Failed(AccessKeyLoginStatus.InUse);
                }
            }
            await activeReader.DisposeAsync();

            var token = Base64Url(RandomNumberGenerator.GetBytes(32));
            await using var upsert = connection.CreateCommand();
            upsert.CommandText = """
                INSERT INTO access_key_sessions(
                    access_key_id, session_hash, device_hash, device_name,
                    client_ip, user_agent, first_seen_utc, last_seen_utc)
                VALUES(
                    $key_id, $session_hash, $device_hash, $device_name,
                    $client_ip, $user_agent, $now, $now)
                ON CONFLICT(access_key_id) DO UPDATE SET
                    session_hash = excluded.session_hash,
                    device_hash = excluded.device_hash,
                    device_name = excluded.device_name,
                    client_ip = excluded.client_ip,
                    user_agent = excluded.user_agent,
                    first_seen_utc = excluded.first_seen_utc,
                    last_seen_utc = excluded.last_seen_utc
                """;
            upsert.Parameters.AddWithValue("$key_id", keyId);
            upsert.Parameters.AddWithValue("$session_hash", Hash(token));
            upsert.Parameters.AddWithValue("$device_hash", deviceHash);
            upsert.Parameters.AddWithValue("$device_name", (object?)Truncate(deviceName, 120) ?? DBNull.Value);
            upsert.Parameters.AddWithValue("$client_ip", (object?)clientIp ?? DBNull.Value);
            upsert.Parameters.AddWithValue("$user_agent", (object?)Truncate(userAgent, 500) ?? DBNull.Value);
            upsert.Parameters.AddWithValue("$now", FormatTimestamp(now));
            await upsert.ExecuteNonQueryAsync(cancellationToken);

            return new AccessKeyLoginResult(
                AccessKeyLoginStatus.Success,
                token,
                new AccessSessionDto(true, "key", label, expiresAt, false, _deviceLeaseSeconds));
        }
        finally
        {
            _writeLock.Release();
        }
    }

    public async Task LogoutAsync(string? sessionToken, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(sessionToken)) return;
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "DELETE FROM access_key_sessions WHERE session_hash = $session_hash";
        command.Parameters.AddWithValue("$session_hash", Hash(sessionToken));
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<IReadOnlyList<AccessKeyDto>> GetKeysAsync(CancellationToken cancellationToken = default)
    {
        var now = DateTimeOffset.UtcNow;
        var items = new List<AccessKeyDto>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT k.id, k.key_hint, k.label, k.expires_at_utc, k.is_active,
                   k.created_at_utc, s.device_name, s.client_ip, s.last_seen_utc
            FROM access_keys k
            LEFT JOIN access_key_sessions s ON s.access_key_id = k.id
            ORDER BY k.id DESC
            """;
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var expiresAt = ParseTimestamp(reader.GetString(3));
            DateTimeOffset? lastSeen = reader.IsDBNull(8) ? null : ParseTimestamp(reader.GetString(8));
            items.Add(new AccessKeyDto(
                reader.GetInt64(0),
                reader.GetString(1),
                reader.GetString(2),
                expiresAt,
                reader.GetInt64(4) == 1,
                expiresAt <= now,
                ParseTimestamp(reader.GetString(5)),
                reader.IsDBNull(6) ? null : reader.GetString(6),
                reader.IsDBNull(7) ? null : reader.GetString(7),
                lastSeen,
                lastSeen is not null && (now - lastSeen.Value).TotalSeconds < _deviceLeaseSeconds));
        }
        return items;
    }

    public async Task<CreatedAccessKeyDto> CreateKeyAsync(
        CreateAccessKeyRequest request,
        CancellationToken cancellationToken = default)
    {
        var days = Math.Clamp(request.ValidDays, 1, 3650);
        var now = DateTimeOffset.UtcNow;
        var expiresAt = now.AddDays(days);
        var label = string.IsNullOrWhiteSpace(request.Label)
            ? $"Người dùng {now:yyyyMMdd-HHmm}"
            : Truncate(request.Label.Trim(), 120)!;

        await _writeLock.WaitAsync(cancellationToken);
        try
        {
            await using var connection = await OpenAsync(cancellationToken);
            for (var attempt = 0; attempt < 5; attempt++)
            {
                var rawKey = GenerateKey();
                await using var command = connection.CreateCommand();
                command.CommandText = """
                    INSERT INTO access_keys(
                        key_hash, key_hint, label, expires_at_utc,
                        is_active, created_at_utc, updated_at_utc)
                    VALUES($key_hash, $key_hint, $label, $expires_at, 1, $now, $now);
                    SELECT last_insert_rowid();
                    """;
                command.Parameters.AddWithValue("$key_hash", Hash(NormalizeKey(rawKey)));
                command.Parameters.AddWithValue("$key_hint", MaskKey(rawKey));
                command.Parameters.AddWithValue("$label", label);
                command.Parameters.AddWithValue("$expires_at", FormatTimestamp(expiresAt));
                command.Parameters.AddWithValue("$now", FormatTimestamp(now));
                try
                {
                    var id = Convert.ToInt64(await command.ExecuteScalarAsync(cancellationToken));
                    return new CreatedAccessKeyDto(id, rawKey, MaskKey(rawKey), label, expiresAt, true, now);
                }
                catch (SqliteException ex) when (ex.SqliteErrorCode == 19 && attempt < 4)
                {
                    // Extremely unlikely random-key collision; generate another key.
                }
            }
        }
        finally
        {
            _writeLock.Release();
        }

        throw new InvalidOperationException("Không thể tạo key mới.");
    }

    public async Task<bool> SetKeyActiveAsync(
        long id,
        bool isActive,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var transaction = (SqliteTransaction)await connection.BeginTransactionAsync(cancellationToken);
        await using var update = connection.CreateCommand();
        update.Transaction = transaction;
        update.CommandText = """
            UPDATE access_keys
            SET is_active = $is_active, updated_at_utc = $now
            WHERE id = $id
            """;
        update.Parameters.AddWithValue("$is_active", isActive ? 1 : 0);
        update.Parameters.AddWithValue("$now", FormatTimestamp(DateTimeOffset.UtcNow));
        update.Parameters.AddWithValue("$id", id);
        var changed = await update.ExecuteNonQueryAsync(cancellationToken) > 0;
        if (changed && !isActive)
        {
            await using var delete = connection.CreateCommand();
            delete.Transaction = transaction;
            delete.CommandText = "DELETE FROM access_key_sessions WHERE access_key_id = $id";
            delete.Parameters.AddWithValue("$id", id);
            await delete.ExecuteNonQueryAsync(cancellationToken);
        }
        await transaction.CommitAsync(cancellationToken);
        return changed;
    }

    public async Task<bool> DisconnectKeyAsync(long id, CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "DELETE FROM access_key_sessions WHERE access_key_id = $id";
        command.Parameters.AddWithValue("$id", id);
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    public async Task<bool> DeleteKeyAsync(long id, CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "DELETE FROM access_keys WHERE id = $id";
        command.Parameters.AddWithValue("$id", id);
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    private AccessKeyLoginResult Failed(AccessKeyLoginStatus status, bool expired = false) =>
        new(status, null, AccessSessionDto.Denied(_deviceLeaseSeconds, expired));

    private async Task<SqliteConnection> OpenAsync(CancellationToken cancellationToken)
    {
        var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "PRAGMA busy_timeout=10000;";
        await command.ExecuteNonQueryAsync(cancellationToken);
        return connection;
    }

    private static async Task DeleteSessionAsync(
        SqliteConnection connection,
        long keyId,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = "DELETE FROM access_key_sessions WHERE access_key_id = $key_id";
        command.Parameters.AddWithValue("$key_id", keyId);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static bool ValidDeviceId(string? value) =>
        !string.IsNullOrWhiteSpace(value) && value.Trim().Length is >= 16 and <= 160;

    private static string NormalizeKey(string value) => value.Trim().ToUpperInvariant();

    private static string GenerateKey()
    {
        const string alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
        var bytes = RandomNumberGenerator.GetBytes(20);
        var chars = bytes.Select(value => alphabet[value % alphabet.Length]).ToArray();
        return $"GRD-{new string(chars, 0, 5)}-{new string(chars, 5, 5)}-{new string(chars, 10, 5)}-{new string(chars, 15, 5)}";
    }

    private static string MaskKey(string value) => $"{value[..9]}-*****-*****-{value[^5..]}";

    private static string Base64Url(byte[] value) =>
        Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static string Hash(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));

    private static string FormatTimestamp(DateTimeOffset value) => value.UtcDateTime.ToString("O");

    private static DateTimeOffset ParseTimestamp(string value) =>
        DateTimeOffset.Parse(value, null, System.Globalization.DateTimeStyles.RoundtripKind);

    private static string? Truncate(string? value, int maxLength) =>
        string.IsNullOrWhiteSpace(value) ? null : value.Trim()[..Math.Min(value.Trim().Length, maxLength)];
}
