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
            round_number INTEGER,
            round_local_date TEXT,
            source_serial TEXT NOT NULL DEFAULT '',
            item_code TEXT NOT NULL,
            item_name TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('VEGETABLE', 'MEAT', 'SPECIAL')),
            detected_at_utc TEXT NOT NULL,
            confidence REAL NOT NULL,
            sequence_json TEXT NOT NULL,
            detection_reason TEXT NOT NULL,
            capture_path TEXT,
            created_at_utc TEXT NOT NULL
        );

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

        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL UNIQUE,
            display_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS system_event_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL,
            response_status INTEGER,
            response_body TEXT,
            created_at_utc TEXT NOT NULL,
            attempted_at_utc TEXT,
            FOREIGN KEY(event_id) REFERENCES scanner_events(id)
        );
        """;

    private const string Indexes = """
        CREATE INDEX IF NOT EXISTS ix_results_detected_at ON results(detected_at_utc DESC);
        CREATE INDEX IF NOT EXISTS ix_results_category_detected_at
            ON results(category, detected_at_utc DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_results_source_round
            ON results(source_serial, round_local_date, round_number)
            WHERE round_number IS NOT NULL AND round_local_date IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_scanner_events_occurred_at
            ON scanner_events(occurred_at_utc DESC);
        CREATE INDEX IF NOT EXISTS ix_subscribers_active ON subscribers(is_active, id DESC);
        """;

    private static readonly string[] ItemCodes =
    [
        "CA_ROT", "NGO", "CAI", "CA_CHUA",
        "BANH_MI", "XIEN", "DUI", "BO", "PIZZA", "SALAD"
    ];

    private readonly string _connectionString;
    private readonly TimeZoneInfo _timeZone;
    private readonly int _offlineAfterSeconds;
    private readonly int _dayBoundaryHour;

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
        _offlineAfterSeconds = configuration.GetValue("Dashboard:ScannerOfflineAfterSeconds", 10);
        _dayBoundaryHour = Math.Clamp(
            configuration.GetValue("Dashboard:DayBoundaryHour", 23), 0, 23);
    }

    public DateOnly LocalToday
    {
        get
        {
            var local = TimeZoneInfo.ConvertTime(DateTimeOffset.UtcNow, _timeZone);
            return GetBusinessDate(local);
        }
    }

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await ExecuteAsync(connection, Schema, cancellationToken);
        await MigrateResultsAsync(connection, cancellationToken);
        await ExecuteAsync(connection, Indexes, cancellationToken);
    }

    private async Task<SqliteConnection> OpenAsync(CancellationToken cancellationToken = default)
    {
        var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(cancellationToken);
        await ExecuteAsync(connection, "PRAGMA busy_timeout=10000;", cancellationToken);
        return connection;
    }

    private static async Task ExecuteAsync(
        SqliteConnection connection,
        string sql,
        CancellationToken cancellationToken,
        SqliteTransaction? transaction = null)
    {
        await using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task MigrateResultsAsync(
        SqliteConnection connection,
        CancellationToken cancellationToken)
    {
        await using var schemaCommand = connection.CreateCommand();
        schemaCommand.CommandText = "SELECT sql FROM sqlite_master WHERE type='table' AND name='results'";
        var sql = Convert.ToString(await schemaCommand.ExecuteScalarAsync(cancellationToken)) ?? string.Empty;
        if (sql.Contains("'SPECIAL'", StringComparison.OrdinalIgnoreCase) &&
            sql.Contains("round_number", StringComparison.OrdinalIgnoreCase) &&
            sql.Contains("source_serial", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        await ExecuteAsync(connection, "PRAGMA foreign_keys=OFF;", cancellationToken);
        await using var transaction = (SqliteTransaction)await connection.BeginTransactionAsync(cancellationToken);
        const string migration = """
            DROP TABLE IF EXISTS results_v2;
            CREATE TABLE results_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_number INTEGER,
                round_local_date TEXT,
                source_serial TEXT NOT NULL DEFAULT '',
                item_code TEXT NOT NULL,
                item_name TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('VEGETABLE', 'MEAT', 'SPECIAL')),
                detected_at_utc TEXT NOT NULL,
                confidence REAL NOT NULL,
                sequence_json TEXT NOT NULL,
                detection_reason TEXT NOT NULL,
                capture_path TEXT,
                created_at_utc TEXT NOT NULL
            );
            INSERT INTO results_v2(
                id, round_number, round_local_date, source_serial,
                item_code, item_name, category, detected_at_utc, confidence,
                sequence_json, detection_reason, capture_path, created_at_utc
            )
            SELECT id, NULL, NULL, '127.0.0.1:5575',
                   item_code, item_name, category, detected_at_utc, confidence,
                   sequence_json, detection_reason, capture_path, created_at_utc
            FROM results;
            DROP TABLE results;
            ALTER TABLE results_v2 RENAME TO results;
            """;
        await ExecuteAsync(connection, migration, cancellationToken, transaction);
        await transaction.CommitAsync(cancellationToken);
        await ExecuteAsync(connection, "PRAGMA foreign_keys=ON;", cancellationToken);
    }

    public async Task<PageDto<ResultDto>> GetResultsAsync(
        int page,
        int pageSize,
        DateOnly localDate,
        CancellationToken cancellationToken = default)
    {
        var rows = await LoadResultsForDateAsync(localDate, cancellationToken);
        rows = ApplyStreakLengths(rows);
        var descending = rows.AsEnumerable().Reverse().ToList();
        var items = descending.Skip((page - 1) * pageSize).Take(pageSize).ToList();
        var totalPages = rows.Count == 0 ? 0 : (int)Math.Ceiling(rows.Count / (double)pageSize);
        return new PageDto<ResultDto>(items, page, pageSize, rows.Count, totalPages);
    }

    public Task<TodayStatsDto> GetTodayStatsAsync(CancellationToken cancellationToken = default) =>
        GetStatsForDateAsync(LocalToday, cancellationToken);

    public async Task<CompactTodayStatsDto> GetCompactStatsForDateAsync(
        DateOnly localDate,
        CancellationToken cancellationToken = default)
    {
        var stats = await GetStatsForDateAsync(localDate, cancellationToken);
        return new CompactTodayStatsDto(
            stats.LocalDate,
            stats.TotalResults,
            stats.RoundCount,
            stats.MissedRoundCount,
            stats.VegetableCount,
            stats.MeatCount,
            stats.SpecialCount,
            stats.CurrentVegetableStreak,
            stats.CurrentMeatStreak,
            stats.LongestVegetableStreak,
            stats.LongestMeatStreak,
            stats.ItemCounts,
            stats.VegetableRuns.Select(ToRunSummary).ToList(),
            stats.MeatRuns.Select(ToRunSummary).ToList(),
            stats.VegetableStreakBuckets
                .Select(bucket => new StreakBucketSummaryDto(bucket.Length, bucket.Count))
                .ToList(),
            stats.MeatStreakBuckets
                .Select(bucket => new StreakBucketSummaryDto(bucket.Length, bucket.Count))
                .ToList(),
            stats.LatestResult);
    }

    public async Task<TodayStatsDto> GetStatsForDateAsync(
        DateOnly localDate,
        CancellationToken cancellationToken = default)
    {
        var rows = ApplyStreakLengths(await LoadResultsForDateAsync(localDate, cancellationToken));
        var maximumRecordedRound = rows
            .Where(row => row.RoundNumber.HasValue)
            .Select(row => row.RoundNumber!.Value)
            .DefaultIfEmpty(0)
            .Max();
        var observedRound = maximumRecordedRound;
        if (localDate == LocalToday)
        {
            var scanner = await GetScannerStatusAsync(cancellationToken);
            observedRound = Math.Max(observedRound, scanner.CurrentRound ?? 0);
        }
        var roundCount = Math.Max((long)observedRound, rows.LongCount());
        var missedRoundCount = Math.Max(0L, roundCount - rows.LongCount());
        var runs = BuildRuns(rows);
        var latest = rows.LastOrDefault();
        var currentRun = runs.LastOrDefault();
        var currentVegetable = currentRun?.Category == "VEGETABLE" ? currentRun.Length : 0;
        var currentMeat = currentRun?.Category == "MEAT" ? currentRun.Length : 0;
        var vegetableRuns = runs.Where(run => run.Category == "VEGETABLE").ToList();
        var meatRuns = runs.Where(run => run.Category == "MEAT").ToList();
        var itemCounts = ItemCodes.ToDictionary(code => code, _ => 0L);
        foreach (var group in rows.GroupBy(row => row.ItemCode))
        {
            itemCounts[group.Key] = group.LongCount();
        }

        return new TodayStatsDto(
            localDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            rows.Count,
            roundCount,
            missedRoundCount,
            rows.LongCount(row => row.Category == "VEGETABLE"),
            rows.LongCount(row => row.Category == "MEAT"),
            rows.LongCount(row => row.Category == "SPECIAL"),
            currentVegetable,
            currentMeat,
            vegetableRuns.Count == 0 ? 0 : vegetableRuns.Max(run => run.Length),
            meatRuns.Count == 0 ? 0 : meatRuns.Max(run => run.Length),
            itemCounts,
            vegetableRuns,
            meatRuns,
            BuildBuckets(vegetableRuns),
            BuildBuckets(meatRuns),
            latest);
    }

    public async Task<IReadOnlyList<DailySummaryDto>> GetDailySummariesAsync(
        int limit,
        CancellationToken cancellationToken = default)
    {
        var dates = new List<DateOnly>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "SELECT detected_at_utc FROM results ORDER BY detected_at_utc DESC";
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var localDate = GetBusinessDate(TimeZoneInfo.ConvertTime(
                ParseUtc(reader.GetString(0)), _timeZone));
            if (!dates.Contains(localDate))
            {
                dates.Add(localDate);
                if (dates.Count >= limit)
                {
                    break;
                }
            }
        }

        var summaries = new List<DailySummaryDto>();
        foreach (var date in dates)
        {
            var stats = await GetStatsForDateAsync(date, cancellationToken);
            summaries.Add(new DailySummaryDto(
                stats.LocalDate,
                stats.TotalResults,
                stats.RoundCount,
                stats.MissedRoundCount,
                stats.VegetableCount,
                stats.MeatCount,
                stats.SpecialCount,
                stats.LongestVegetableStreak,
                stats.LongestMeatStreak));
        }
        return summaries;
    }

    private async Task<List<ResultDto>> LoadResultsForDateAsync(
        DateOnly localDate,
        CancellationToken cancellationToken)
    {
        var (startUtc, endUtc) = GetUtcRange(localDate);
        var rows = new List<ResultDto>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, round_number, round_local_date, source_serial,
                   item_code, item_name, category, detected_at_utc, confidence,
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
        return rows;
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
        command.Parameters.AddWithValue("$severity", request.Severity.ToUpperInvariant());
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
        int? currentRound = null;
        if (state.TryGetValue("scanner_current_round", out var roundRaw) && int.TryParse(roundRaw, out var parsedRound))
        {
            currentRound = parsedRound;
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
            _offlineAfterSeconds,
            state.GetValueOrDefault("scanner_source_serial"),
            currentRound);
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
        var streakCategory = GetStreakCategory(latest);
        var isVegetable = streakCategory == "VEGETABLE";
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
            category = streakCategory,
            streakLength = streak,
            threshold,
            resultId = latest.Id,
            roundNumber = latest.RoundNumber,
            itemCode = latest.ItemCode,
            itemName = latest.ItemName,
            detectedAtUtc = latest.DetectedAtUtc,
            localDate = stats.LocalDate
        });
        return new AlertCandidate(
            alertKey, rule, streakCategory, streak, latest.Id, payload);
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
            UPDATE alert_deliveries SET status='PENDING'
            WHERE alert_key=$alertKey AND status='PENDING_CONFIGURATION' AND $configured=1;
            SELECT status FROM alert_deliveries WHERE alert_key=$alertKey;
            """;
        command.Parameters.AddWithValue("$alertKey", candidate.AlertKey);
        command.Parameters.AddWithValue("$ruleCode", candidate.RuleCode);
        command.Parameters.AddWithValue("$category", candidate.Category);
        command.Parameters.AddWithValue("$streakLength", candidate.StreakLength);
        command.Parameters.AddWithValue("$resultId", candidate.ResultId);
        command.Parameters.AddWithValue("$status", initialStatus);
        command.Parameters.AddWithValue("$configured", webhookConfigured ? 1 : 0);
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

    public async Task<IReadOnlyList<SubscriberDto>> GetSubscribersAsync(
        bool activeOnly,
        CancellationToken cancellationToken = default)
    {
        var items = new List<SubscriberDto>();
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, phone_number, display_name, is_active, created_at_utc, updated_at_utc
            FROM subscribers
            WHERE $activeOnly=0 OR is_active=1
            ORDER BY id DESC
            """;
        command.Parameters.AddWithValue("$activeOnly", activeOnly ? 1 : 0);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(ReadSubscriber(reader));
        }
        return items;
    }

    public async Task<SubscriberDto> UpsertSubscriberAsync(
        string phoneNumber,
        string? displayName,
        CancellationToken cancellationToken = default)
    {
        var now = ToUtcText(DateTime.UtcNow);
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO subscribers(phone_number, display_name, is_active, created_at_utc, updated_at_utc)
            VALUES ($phone, $name, 1, $now, $now)
            ON CONFLICT(phone_number) DO UPDATE SET
                display_name=excluded.display_name,
                is_active=1,
                updated_at_utc=excluded.updated_at_utc;
            SELECT id, phone_number, display_name, is_active, created_at_utc, updated_at_utc
            FROM subscribers WHERE phone_number=$phone;
            """;
        command.Parameters.AddWithValue("$phone", phoneNumber);
        command.Parameters.AddWithValue("$name", (object?)displayName ?? DBNull.Value);
        command.Parameters.AddWithValue("$now", now);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        await reader.ReadAsync(cancellationToken);
        return ReadSubscriber(reader);
    }

    public async Task<bool> DeactivateSubscriberAsync(
        long id,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE subscribers SET is_active=0, updated_at_utc=$now WHERE id=$id
            """;
        command.Parameters.AddWithValue("$id", id);
        command.Parameters.AddWithValue("$now", ToUtcText(DateTime.UtcNow));
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    public async Task<SystemEventDeliveryCandidate?> GetNextSystemEventDeliveryAsync(
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT e.id, e.severity, e.event_code, e.message, e.details_json, e.occurred_at_utc
            FROM scanner_events e
            LEFT JOIN system_event_deliveries d ON d.event_id=e.id
            WHERE e.severity IN ('ERROR', 'CRITICAL')
              AND (d.id IS NULL OR d.status IN ('PENDING', 'PENDING_CONFIGURATION'))
            ORDER BY e.id ASC
            LIMIT 1
            """;
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken))
        {
            return null;
        }
        return new SystemEventDeliveryCandidate(
            reader.GetInt64(0),
            reader.GetString(1),
            reader.GetString(2),
            reader.GetString(3),
            ParseJson(reader.GetString(4)),
            ParseUtc(reader.GetString(5)));
    }

    public async Task<string> EnsureSystemEventDeliveryAsync(
        long eventId,
        bool webhookConfigured,
        CancellationToken cancellationToken = default)
    {
        var status = webhookConfigured ? "PENDING" : "PENDING_CONFIGURATION";
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT OR IGNORE INTO system_event_deliveries(event_id, status, created_at_utc)
            VALUES ($eventId, $status, $now);
            UPDATE system_event_deliveries SET status='PENDING'
            WHERE event_id=$eventId AND status='PENDING_CONFIGURATION' AND $configured=1;
            SELECT status FROM system_event_deliveries WHERE event_id=$eventId;
            """;
        command.Parameters.AddWithValue("$eventId", eventId);
        command.Parameters.AddWithValue("$status", status);
        command.Parameters.AddWithValue("$configured", webhookConfigured ? 1 : 0);
        command.Parameters.AddWithValue("$now", ToUtcText(DateTime.UtcNow));
        return Convert.ToString(await command.ExecuteScalarAsync(cancellationToken))!;
    }

    public async Task CompleteSystemEventDeliveryAsync(
        long eventId,
        string status,
        int? responseStatus,
        string? responseBody,
        CancellationToken cancellationToken = default)
    {
        await using var connection = await OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE system_event_deliveries
            SET status=$status, response_status=$responseStatus,
                response_body=$responseBody, attempted_at_utc=$attemptedAt
            WHERE event_id=$eventId
            """;
        command.Parameters.AddWithValue("$status", status);
        command.Parameters.AddWithValue("$responseStatus", (object?)responseStatus ?? DBNull.Value);
        command.Parameters.AddWithValue("$responseBody", (object?)responseBody ?? DBNull.Value);
        command.Parameters.AddWithValue("$attemptedAt", ToUtcText(DateTime.UtcNow));
        command.Parameters.AddWithValue("$eventId", eventId);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<PredictionContextDto> GetPredictionContextAsync(
        DateOnly localDate,
        IReadOnlyDictionary<string, double> payoutMultipliers,
        CancellationToken cancellationToken = default)
    {
        var historicalCounts = ItemCodes.ToDictionary(code => code, _ => 0L);
        var history = new List<PredictionHistoryItemDto>();
        await using var connection = await OpenAsync(cancellationToken);
        await using (var countCommand = connection.CreateCommand())
        {
            countCommand.CommandText = "SELECT item_code, COUNT(*) FROM results GROUP BY item_code";
            await using var reader = await countCommand.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
            {
                historicalCounts[reader.GetString(0)] = reader.GetInt64(1);
            }
        }
        await using (var historyCommand = connection.CreateCommand())
        {
            historyCommand.CommandText = """
                SELECT id, round_number, round_local_date, source_serial,
                       item_code, category, detected_at_utc
                FROM results
                ORDER BY detected_at_utc DESC, id DESC
                """;
            await using var reader = await historyCommand.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
            {
                var detectedAt = ParseUtc(reader.GetString(6));
                var businessDate = GetBusinessDate(
                    TimeZoneInfo.ConvertTime(detectedAt, _timeZone));
                history.Add(new PredictionHistoryItemDto(
                    reader.GetInt64(0),
                    reader.IsDBNull(1) ? null : reader.GetInt32(1),
                    businessDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    reader.GetString(3),
                    reader.GetString(4),
                    reader.GetString(5),
                    detectedAt));
            }
        }
        history.Reverse();
        var today = await GetStatsForDateAsync(localDate, cancellationToken);
        return new PredictionContextDto(
            localDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            payoutMultipliers,
            historicalCounts,
            today.ItemCounts,
            history);
    }

    private static IReadOnlyList<StreakBucketDto> BuildBuckets(
        IReadOnlyList<StreakRunDto> runs) =>
        runs.Where(run => run.Length >= 2)
            .GroupBy(run => run.Length)
            .OrderBy(group => group.Key)
            .Select(group => new StreakBucketDto(group.Key, group.Count(), group.ToList()))
            .ToList();

    private static List<ResultDto> ApplyStreakLengths(IReadOnlyList<ResultDto> rows)
    {
        var output = rows.ToList();
        var index = 0;
        while (index < output.Count)
        {
            var end = index + 1;
            while (end < output.Count && CanContinueRun(output[end - 1], output[end]))
            {
                end++;
            }
            var length = end - index;
            for (var itemIndex = index; itemIndex < end; itemIndex++)
            {
                output[itemIndex] = output[itemIndex] with { StreakLength = length };
            }
            index = end;
        }
        return output;
    }

    private static List<StreakRunDto> BuildRuns(IReadOnlyList<ResultDto> rows)
    {
        var runs = new List<StreakRunDto>();
        if (rows.Count == 0)
        {
            return runs;
        }
        var runItems = new List<ResultDto> { rows[0] };
        var previous = rows[0];
        for (var index = 1; index < rows.Count; index++)
        {
            var current = rows[index];
            if (CanContinueRun(previous, current))
            {
                runItems.Add(current);
            }
            else
            {
                runs.Add(ToRun(runItems));
                runItems = new List<ResultDto> { current };
            }
            previous = current;
        }
        runs.Add(ToRun(runItems));
        return runs;
    }

    private static bool CanContinueRun(ResultDto previous, ResultDto current)
    {
        if (GetStreakCategory(current) != GetStreakCategory(previous) ||
            current.SourceSerial != previous.SourceSerial)
        {
            return false;
        }

        if (!previous.RoundNumber.HasValue && !current.RoundNumber.HasValue)
        {
            return true;
        }

        return previous.RoundNumber.HasValue &&
               current.RoundNumber.HasValue &&
               previous.RoundLocalDate == current.RoundLocalDate &&
               current.RoundNumber.Value == previous.RoundNumber.Value + 1;
    }

    private static StreakRunDto ToRun(IReadOnlyList<ResultDto> results)
    {
        var start = results[0];
        var end = results[^1];
        var items = results
            .Select(result => new StreakRunItemDto(
                result.Id,
                result.RoundNumber,
                result.ItemCode,
                result.ItemName,
                result.DetectedAtUtc))
            .ToList();

        return new StreakRunDto(
            GetStreakCategory(end),
            results.Count,
            start.Id,
            end.Id,
            start.RoundNumber,
            end.RoundNumber,
            start.DetectedAtUtc,
            end.DetectedAtUtc,
            items);
    }

    private static StreakRunSummaryDto ToRunSummary(StreakRunDto run) => new(
        run.Category,
        run.Length,
        run.StartResultId,
        run.EndResultId,
        run.StartRound,
        run.EndRound,
        run.StartedAtUtc,
        run.EndedAtUtc);

    private static string GetStreakCategory(ResultDto result) => result.ItemCode switch
    {
        "SALAD" => "VEGETABLE",
        "PIZZA" => "MEAT",
        _ => result.Category
    };

    private (DateTime StartUtc, DateTime EndUtc) GetUtcRange(DateOnly localDate)
    {
        var endLocal = DateTime.SpecifyKind(
            localDate.ToDateTime(new TimeOnly(_dayBoundaryHour, 0)),
            DateTimeKind.Unspecified);
        var startLocal = endLocal.AddDays(-1);
        return (
            TimeZoneInfo.ConvertTimeToUtc(startLocal, _timeZone),
            TimeZoneInfo.ConvertTimeToUtc(endLocal, _timeZone));
    }

    private DateOnly GetBusinessDate(DateTimeOffset localTime)
    {
        var date = DateOnly.FromDateTime(localTime.DateTime);
        return localTime.Hour >= _dayBoundaryHour && _dayBoundaryHour != 0
            ? date.AddDays(1)
            : date;
    }

    private static ResultDto ReadResult(SqliteDataReader reader) =>
        new(
            reader.GetInt64(0),
            reader.IsDBNull(1) ? null : reader.GetInt32(1),
            reader.IsDBNull(2) ? null : reader.GetString(2),
            reader.GetString(3),
            reader.GetString(4),
            reader.GetString(5),
            reader.GetString(6),
            ParseUtc(reader.GetString(7)),
            reader.GetDouble(8),
            JsonSerializer.Deserialize<List<string>>(reader.GetString(9)) ?? [],
            reader.GetString(10),
            reader.IsDBNull(11) ? null : reader.GetString(11),
            0);

    private static SubscriberDto ReadSubscriber(SqliteDataReader reader) =>
        new(
            reader.GetInt64(0),
            reader.GetString(1),
            reader.IsDBNull(2) ? null : reader.GetString(2),
            reader.GetInt64(3) != 0,
            ParseUtc(reader.GetString(4)),
            ParseUtc(reader.GetString(5)));

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
