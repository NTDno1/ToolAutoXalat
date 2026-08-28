using System.Globalization;
using System.Text.Json;
using GreedyStats.Api.Models;
using Microsoft.Data.Sqlite;

namespace GreedyStats.Api.Data;

public sealed class GreedyDatabase
{
    private const string Schema = """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA busy_timeout=10000;

        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_code TEXT NOT NULL,
            item_name TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('VEGETABLE', 'MEAT')),
            detected_at_utc TEXT NOT NULL,
            confidence REAL NOT NULL,
            sequence_json TEXT NOT NULL,
            detection_reason TEXT NOT NULL,
            capture_path TEXT,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_results_detected_at ON results(detected_at_utc DESC);
        CREATE INDEX IF NOT EXISTS ix_results_category_detected_at
            ON results(category, detected_at_utc DESC);

        CREATE TABLE IF NOT EXISTS scanner_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE,
            severity TEXT NOT NULL,
            event_code TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            occurred_at_utc TEXT NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS ix_scanner_events_occurred_at
            ON scanner_events(occurred_at_utc DESC);

        CREATE TABLE IF NOT EXISTS scanner_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alert_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL UNIQUE,
            rule_code TEXT NOT NULL,
            category TEXT NOT NULL,
            streak_length INTEGER NOT NULL,
            result_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            response_status INTEGER,
            response_body TEXT,
            created_at_utc TEXT NOT NULL,
            attempted_at_utc TEXT,
            FOREIGN KEY(result_id) REFERENCES results(id)
        );
        """;

    private readonly string _connectionString;
    private readonly TimeZoneInfo _timeZone;
    private readonly int _offlineAfterSeconds;

    public GreedyDatabase(IConfiguration configuration, IWebHostEnvironment environment)
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
        _timeZone = ResolveTimeZone(configuration["Dashboard:TimeZoneId"]);
        _offlineAfterSeconds = configuration.GetValue("Dashboard:ScannerOfflineAfterSeconds", 15);
    }

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = Schema;
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private async Task<SqliteConnection> OpenAsync(CancellationToken cancellationToken = default)
    {
        var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "PRAGMA busy_timeout=10000;";
        await command.ExecuteNonQueryAsync(cancellationToken);
        return connection;
    }

    public async Task<PageDto<ResultDto>> GetResultsAsync(
        int page,
        int pageSize,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var countCommand = connection.CreateCommand();
        countCommand.CommandText = "SELECT COUNT(*) FROM results";
        var total = Convert.ToInt64(await countCommand.ExecuteScalarAsync(cancellationToken));

        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, item_code, item_name, category, detected_at_utc, confidence,
                   sequence_json, detection_reason, capture_path
            FROM results
            ORDER BY detected_at_utc DESC, id DESC
            LIMIT $limit OFFSET $offset
            """;
        command.Parameters.AddWithValue("$limit", pageSize);
        command.Parameters.AddWithValue("$offset", (page - 1L) * pageSize);

        var items = new List<ResultDto>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(ReadResult(reader));
        }
        var totalPages = total == 0 ? 0 : (int)Math.Ceiling(total / (double)pageSize);
        return new PageDto<ResultDto>(items, page, pageSize, total, totalPages);
    }

    public async Task<TodayStatsDto> GetTodayStatsAsync(
        CancellationToken cancellationToken = default)
    {
        var nowLocal = TimeZoneInfo.ConvertTime(DateTimeOffset.UtcNow, _timeZone);
        var localDate = DateOnly.FromDateTime(nowLocal.DateTime);
        var startLocal = DateTime.SpecifyKind(localDate.ToDateTime(TimeOnly.MinValue), DateTimeKind.Unspecified);
        var endLocal = startLocal.AddDays(1);
        var startUtc = TimeZoneInfo.ConvertTimeToUtc(startLocal, _timeZone);
        var endUtc = TimeZoneInfo.ConvertTimeToUtc(endLocal, _timeZone);

        var rows = new List<ResultDto>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, item_code, item_name, category, detected_at_utc, confidence,
                   sequence_json, detection_reason, capture_path
            FROM results
            WHERE detected_at_utc >= $start AND detected_at_utc < $end
            ORDER BY detected_at_utc ASC, id ASC
            """;
        command.Parameters.AddWithValue("$start", ToUtcText(startUtc));
        command.Parameters.AddWithValue("$end", ToUtcText(endUtc));
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            rows.Add(ReadResult(reader));
        }

        var runs = BuildRuns(rows);
        var latest = rows.LastOrDefault();
        var currentVegetable = latest?.Category == "VEGETABLE" ? runs.Last().Length : 0;
        var currentMeat = latest?.Category == "MEAT" ? runs.Last().Length : 0;
        var vegetableRuns = runs.Where(run => run.Category == "VEGETABLE").ToList();
        var meatRuns = runs.Where(run => run.Category == "MEAT").ToList();
        var itemCounts = rows
            .GroupBy(row => row.ItemCode)
            .ToDictionary(group => group.Key, group => (long)group.Count());

        return new TodayStatsDto(
            localDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            rows.Count,
            rows.LongCount(row => row.Category == "VEGETABLE"),
            rows.LongCount(row => row.Category == "MEAT"),
            currentVegetable,
            currentMeat,
            vegetableRuns.Count == 0 ? 0 : vegetableRuns.Max(run => run.Length),
            meatRuns.Count == 0 ? 0 : meatRuns.Max(run => run.Length),
            itemCounts,
            vegetableRuns,
            meatRuns,
            latest);
    }

    public async Task<PageDto<ScannerEventDto>> GetEventsAsync(
        int page,
        int pageSize,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var countCommand = connection.CreateCommand();
        countCommand.CommandText = "SELECT COUNT(*) FROM scanner_events";
        var total = Convert.ToInt64(await countCommand.ExecuteScalarAsync(cancellationToken));

        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, severity, event_code, message, details_json,
                   occurred_at_utc, acknowledged
            FROM scanner_events
            ORDER BY occurred_at_utc DESC, id DESC
            LIMIT $limit OFFSET $offset
            """;
        command.Parameters.AddWithValue("$limit", pageSize);
        command.Parameters.AddWithValue("$offset", (page - 1L) * pageSize);
        var items = new List<ScannerEventDto>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(new ScannerEventDto(
                reader.GetInt64(0),
                reader.GetString(1),
                reader.GetString(2),
                reader.GetString(3),
                ParseJson(reader.GetString(4)),
                ParseUtc(reader.GetString(5)),
                reader.GetInt64(6) != 0));
        }
        var totalPages = total == 0 ? 0 : (int)Math.Ceiling(total / (double)pageSize);
        return new PageDto<ScannerEventDto>(items, page, pageSize, total, totalPages);
    }

    public async Task<long> InsertEventAsync(
        ScannerEventRequest request,
        CancellationToken cancellationToken = default)
    {
        var occurredAt = request.OccurredAtUtc ?? DateTimeOffset.UtcNow;
        var sourceKey = string.IsNullOrWhiteSpace(request.SourceKey)
            ? $"backend:{request.EventCode}:{occurredAt:O}"
            : request.SourceKey;
        var details = request.Details?.GetRawText() ?? "{}";
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT OR IGNORE INTO scanner_events(
                source_key, severity, event_code, message, details_json,
                occurred_at_utc
            ) VALUES ($sourceKey, $severity, $eventCode, $message, $details, $occurredAt);
            SELECT id FROM scanner_events WHERE source_key=$sourceKey;
            """;
        command.Parameters.AddWithValue("$sourceKey", sourceKey!);
        command.Parameters.AddWithValue("$severity", request.Severity);
        command.Parameters.AddWithValue("$eventCode", request.EventCode);
        command.Parameters.AddWithValue("$message", request.Message);
        command.Parameters.AddWithValue("$details", details);
        command.Parameters.AddWithValue("$occurredAt", ToUtcText(occurredAt.UtcDateTime));
        return Convert.ToInt64(await command.ExecuteScalarAsync(cancellationToken));
    }

    public async Task<bool> AcknowledgeEventAsync(
        long id,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "UPDATE scanner_events SET acknowledged=1 WHERE id=$id";
        command.Parameters.AddWithValue("$id", id);
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    public async Task<ScannerStatusDto> GetScannerStatusAsync(
        CancellationToken cancellationToken = default)
    {
        var state = new Dictionary<string, string>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "SELECT state_key, state_value FROM scanner_state";
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            state[reader.GetString(0)] = reader.GetString(1);
        }
        DateTimeOffset? heartbeat = null;
        if (state.TryGetValue("scanner_heartbeat_utc", out var heartbeatRaw))
        {
            heartbeat = ParseUtc(heartbeatRaw);
        }
        long? lastResultId = null;
        if (state.TryGetValue("last_result_id", out var resultRaw) && long.TryParse(resultRaw, out var parsedId))
        {
            lastResultId = parsedId;
        }
        IReadOnlyList<string> sequence = Array.Empty<string>();
        if (state.TryGetValue("last_sequence", out var sequenceRaw))
        {
            sequence = JsonSerializer.Deserialize<List<string>>(sequenceRaw) ?? [];
        }
        var online = heartbeat.HasValue &&
                     (DateTimeOffset.UtcNow - heartbeat.Value).TotalSeconds <= _offlineAfterSeconds;
        return new ScannerStatusDto(
            state.GetValueOrDefault("scanner_status", "NOT_STARTED"),
            online,
            heartbeat,
            lastResultId,
            sequence,
            _offlineAfterSeconds);
    }

    public async Task<IReadOnlyList<AlertDeliveryDto>> GetAlertDeliveriesAsync(
        CancellationToken cancellationToken = default)
    {
        var items = new List<AlertDeliveryDto>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, alert_key, rule_code, category, streak_length, result_id,
                   status, response_status, created_at_utc, attempted_at_utc
            FROM alert_deliveries
            ORDER BY id DESC LIMIT 100
            """;
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(new AlertDeliveryDto(
                reader.GetInt64(0),
                reader.GetString(1),
                reader.GetString(2),
                reader.GetString(3),
                reader.GetInt32(4),
                reader.GetInt64(5),
                reader.GetString(6),
                reader.IsDBNull(7) ? null : reader.GetInt32(7),
                ParseUtc(reader.GetString(8)),
                reader.IsDBNull(9) ? null : ParseUtc(reader.GetString(9))));
        }
        return items;
    }

    public async Task<AlertCandidate?> GetCurrentAlertCandidateAsync(
        int vegetableThreshold,
        int meatThreshold,
        CancellationToken cancellationToken = default)
    {
        var stats = await GetTodayStatsAsync(cancellationToken);
        var latest = stats.LatestResult;
        if (latest is null)
        {
            return null;
        }
        var isVegetable = latest.Category == "VEGETABLE";
        var streak = isVegetable ? stats.CurrentVegetableStreak : stats.CurrentMeatStreak;
        var threshold = isVegetable ? vegetableThreshold : meatThreshold;
        if (streak < threshold)
        {
            return null;
        }
        var runs = isVegetable ? stats.VegetableRuns : stats.MeatRuns;
        var currentRun = runs.Last();
        var rule = isVegetable ? "VEGETABLE_STREAK_15" : "MEAT_STREAK_3";
        var alertKey = $"{rule}:{currentRun.StartResultId}";
        var payload = JsonSerializer.SerializeToElement(new
        {
            alertKey,
            ruleCode = rule,
            category = latest.Category,
            streakLength = streak,
            threshold,
            resultId = latest.Id,
            itemCode = latest.ItemCode,
            itemName = latest.ItemName,
            detectedAtUtc = latest.DetectedAtUtc,
            localDate = stats.LocalDate
        });
        return new AlertCandidate(
            alertKey, rule, latest.Category, streak, latest.Id, payload);
    }

    public async Task<string> EnsureAlertAsync(
        AlertCandidate candidate,
        bool webhookConfigured,
        CancellationToken cancellationToken = default)
    {
        var initialStatus = webhookConfigured ? "PENDING" : "PENDING_CONFIGURATION";
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT OR IGNORE INTO alert_deliveries(
                alert_key, rule_code, category, streak_length, result_id, status,
                payload_json, created_at_utc
            ) VALUES (
                $alertKey, $ruleCode, $category, $streakLength, $resultId, $status,
                $payload, $createdAt
            );
            SELECT status FROM alert_deliveries WHERE alert_key=$alertKey;
            """;
        command.Parameters.AddWithValue("$alertKey", candidate.AlertKey);
        command.Parameters.AddWithValue("$ruleCode", candidate.RuleCode);
        command.Parameters.AddWithValue("$category", candidate.Category);
        command.Parameters.AddWithValue("$streakLength", candidate.StreakLength);
        command.Parameters.AddWithValue("$resultId", candidate.ResultId);
        command.Parameters.AddWithValue("$status", initialStatus);
        command.Parameters.AddWithValue("$payload", candidate.Payload.GetRawText());
        command.Parameters.AddWithValue("$createdAt", ToUtcText(DateTime.UtcNow));
        return Convert.ToString(await command.ExecuteScalarAsync(cancellationToken))!;
    }

    public async Task CompleteAlertAsync(
        string alertKey,
        string status,
        int? responseStatus,
        string? responseBody,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE alert_deliveries
            SET status=$status, response_status=$responseStatus,
                response_body=$responseBody, attempted_at_utc=$attemptedAt
            WHERE alert_key=$alertKey
            """;
        command.Parameters.AddWithValue("$status", status);
        command.Parameters.AddWithValue("$responseStatus", (object?)responseStatus ?? DBNull.Value);
        command.Parameters.AddWithValue("$responseBody", (object?)responseBody ?? DBNull.Value);
        command.Parameters.AddWithValue("$attemptedAt", ToUtcText(DateTime.UtcNow));
        command.Parameters.AddWithValue("$alertKey", alertKey);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static List<StreakRunDto> BuildRuns(IReadOnlyList<ResultDto> rows)
    {
        var runs = new List<StreakRunDto>();
        if (rows.Count == 0)
        {
            return runs;
        }
        var start = rows[0];
        var previous = rows[0];
        var length = 1;
        for (var index = 1; index < rows.Count; index++)
        {
            var current = rows[index];
            if (current.Category == previous.Category)
            {
                length++;
            }
            else
            {
                runs.Add(new StreakRunDto(
                    previous.Category,
                    length,
                    start.Id,
                    previous.Id,
                    start.DetectedAtUtc,
                    previous.DetectedAtUtc));
                start = current;
                length = 1;
            }
            previous = current;
        }
        runs.Add(new StreakRunDto(
            previous.Category,
            length,
            start.Id,
            previous.Id,
            start.DetectedAtUtc,
            previous.DetectedAtUtc));
        return runs;
    }

    private static ResultDto ReadResult(SqliteDataReader reader)
    {
        return new ResultDto(
            reader.GetInt64(0),
            reader.GetString(1),
            reader.GetString(2),
            reader.GetString(3),
            ParseUtc(reader.GetString(4)),
            reader.GetDouble(5),
            JsonSerializer.Deserialize<List<string>>(reader.GetString(6)) ?? [],
            reader.GetString(7),
            reader.IsDBNull(8) ? null : reader.GetString(8));
    }

    private static DateTimeOffset ParseUtc(string value) =>
        DateTimeOffset.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);

    private static JsonElement ParseJson(string value)
    {
        try
        {
            return JsonSerializer.Deserialize<JsonElement>(value);
        }
        catch (JsonException)
        {
            return JsonSerializer.SerializeToElement(new { raw = value });
        }
    }

    private static string ToUtcText(DateTime value) =>
        value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture);

    private static TimeZoneInfo ResolveTimeZone(string? configured)
    {
        foreach (var id in new[] { configured, "Asia/Bangkok", "SE Asia Standard Time" })
        {
            if (string.IsNullOrWhiteSpace(id))
            {
                continue;
            }
            try
            {
                return TimeZoneInfo.FindSystemTimeZoneById(id);
            }
            catch (TimeZoneNotFoundException)
            {
            }
        }
        return TimeZoneInfo.Utc;
    }
}
