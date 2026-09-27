using System.Diagnostics;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using GreedyStats.Api.Data;
using GreedyStats.Api.Models;

namespace GreedyStats.Api.Services;

public sealed class PredictionService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true
    };

    // Groq JSON mode keeps transport output valid. The backend then applies
    // stricter semantic checks (coverage, eight unique items and category sums)
    // before publishing a prediction.
    private static readonly JsonElement AiResponseFormat =
        JsonSerializer.Deserialize<JsonElement>("""{"type":"json_object"}""");

    public static readonly IReadOnlyDictionary<string, double> PayoutMultipliers =
        new Dictionary<string, double>
        {
            ["CA_ROT"] = 5,
            ["NGO"] = 5,
            ["CAI"] = 5,
            ["CA_CHUA"] = 5,
            ["BANH_MI"] = 10,
            ["XIEN"] = 15,
            ["DUI"] = 25,
            ["BO"] = 45
        };

    private static readonly IReadOnlyDictionary<string, (string Name, string Category)> ItemMeta =
        new Dictionary<string, (string, string)>
        {
            ["CA_ROT"] = ("Cà rốt", "VEGETABLE"),
            ["NGO"] = ("Ngô", "VEGETABLE"),
            ["CAI"] = ("Cải", "VEGETABLE"),
            ["CA_CHUA"] = ("Cà chua", "VEGETABLE"),
            ["BANH_MI"] = ("Bánh mì", "MEAT"),
            ["XIEN"] = ("Xiên", "MEAT"),
            ["DUI"] = ("Đùi", "MEAT"),
            ["BO"] = ("Bò", "MEAT")
        };

    // The game board also groups the eight normal outcomes into two visual
    // sides. This signal is independent from the VEGETABLE/MEAT categories.
    private static readonly HashSet<string> RedSideCodes =
    [
        "CA_CHUA", "BANH_MI", "CA_ROT", "BO"
    ];

    // One character per result keeps every database row in the AI payload
    // without exhausting the provider's free-tier token budget.
    private static readonly IReadOnlyDictionary<string, string> CompactItemCodes =
        new Dictionary<string, string>
        {
            ["CA_ROT"] = "R",
            ["NGO"] = "N",
            ["CAI"] = "C",
            ["CA_CHUA"] = "T",
            ["BANH_MI"] = "B",
            ["XIEN"] = "X",
            ["DUI"] = "D",
            ["BO"] = "O",
            ["PIZZA"] = "P",
            ["SALAD"] = "S"
        };

    private readonly GreedyDatabase _database;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IConfiguration _configuration;
    private readonly ILogger<PredictionService> _logger;
    private readonly AiChannelCache _gptCache = new();
    private readonly AiChannelCache _compoundCache = new();
    private readonly SemaphoreSlim _marketContextLock = new(1, 1);
    private string? _marketContextKey;
    private PredictionContextDto? _marketContext;

    public PredictionService(
        GreedyDatabase database,
        IHttpClientFactory httpClientFactory,
        IConfiguration configuration,
        ILogger<PredictionService> logger)
    {
        _database = database;
        _httpClientFactory = httpClientFactory;
        _configuration = configuration;
        _logger = logger;
    }

    public Task<PredictionContextDto> GetContextAsync(
        DateOnly localDate,
        CancellationToken cancellationToken = default)
        => _database.GetPredictionContextAsync(
            localDate,
            PayoutMultipliers,
            cancellationToken);

    public async Task<PredictionDto> GetPredictionAsync(
        DateOnly localDate,
        CancellationToken cancellationToken = default)
    {
        var context = await GetContextAsync(localDate, cancellationToken);
        return BuildHeuristic(context);
    }

    public async Task<MarketPredictionResponseDto> GetMarketPredictionAsync(
        DateOnly localDate,
        CancellationToken cancellationToken = default)
    {
        const int analysisWindow = 500;
        const int fallbackCountdownSeconds = 20;
        var stopwatch = Stopwatch.StartNew();
        var scannerTask = _database.GetScannerStatusAsync(cancellationToken);
        var signalsTask = _database.GetRecentBettingSignalSnapshotsAsync(
            analysisWindow + 20,
            cancellationToken);
        await Task.WhenAll(scannerTask, signalsTask);
        var scanner = await scannerTask;
        var snapshots = await signalsTask;
        var observedSignals = scanner.BettingSignals;
        var activeRound = scanner.ActiveRound ?? observedSignals?.Round;
        var countdownSeconds = scanner.CountdownSeconds ?? (
            observedSignals is not null && observedSignals.Round == activeRound
                ? observedSignals.CountdownSeconds
                : null);
        var liveSignals = observedSignals is not null &&
                          observedSignals.Round == activeRound &&
                          DateTimeOffset.UtcNow - observedSignals.ObservedAtUtc <= TimeSpan.FromSeconds(5)
            ? observedSignals
            : null;
        var hasHotSignal = !string.IsNullOrWhiteSpace(liveSignals?.HotItemCode);
        var hasCoinSignal = liveSignals?.Items.Any(item =>
            item.CoinCount > 0 || item.ActivityPercent > 0) == true;
        var hasOptionalMarketSignal = hasHotSignal || hasCoinSignal;
        var fallbackDeadlineReached = countdownSeconds is > 0 and <= fallbackCountdownSeconds;
        var housePerformance = AnalyzeHousePerformance(snapshots, liveSignals);
        var hotOutcomeStats = AnalyzeHotOutcomes(snapshots, liveSignals?.HotItemCode);
        var matchedRounds = snapshots.Count(snapshot =>
            snapshot.ResultItemCode is not null &&
            PayoutMultipliers.ContainsKey(snapshot.ResultItemCode));
        matchedRounds = Math.Min(analysisWindow, matchedRounds);

        // Recalculate and cache the 500-result baseline as soon as a new round
        // is visible. When HOT/coin OCR arrives later in the same round, only
        // the cheap live-signal merge remains.
        PredictionContextDto? preparedContext = null;
        var preparingRound = activeRound;
        if (localDate == _database.LocalToday && scanner.IsOnline && preparingRound is not null)
        {
            preparedContext = await GetPreparedMarketContextAsync(
                localDate,
                scanner.SourceSerial,
                preparingRound.Value,
                scanner.LastResultId,
                analysisWindow,
                cancellationToken);
        }

        MarketPredictionResponseDto Waiting(string status, string message) => new(
            status,
            message,
            liveSignals?.Round ?? scanner.ActiveRound,
            liveSignals?.CountdownSeconds ?? scanner.CountdownSeconds,
            liveSignals?.ObservedAtUtc,
            liveSignals?.HotItemCode,
            analysisWindow,
            matchedRounds,
            housePerformance,
            hotOutcomeStats,
            null,
            "CPU",
            stopwatch.ElapsedMilliseconds,
            null);

        if (localDate != _database.LocalToday)
        {
            return Waiting(
                "NO_LIVE_ROUND",
                "Mô hình HOT/xu chỉ chốt cho cầu đang chạy của ngày hiện tại.");
        }
        if (!scanner.IsOnline || activeRound is null)
        {
            return Waiting(
                "WAITING_ROUND",
                "Đang chờ scanner nhận diện round mới để tính sẵn nền 500 cầu.");
        }
        if (countdownSeconds <= 0)
        {
            return Waiting(
                "WAITING_NEXT_ROUND",
                "Cầu hiện tại đã khóa cược; đang chờ round mới để tính lại nền dữ liệu.");
        }
        if (!hasOptionalMarketSignal && !fallbackDeadlineReached)
        {
            return Waiting(
                "WAITING_SIGNAL",
                $"Đã tính xong nền 500 cầu. Chờ HOT/xu tối đa 10 giây đầu; nếu không có sẽ tự chốt khi đồng hồ còn {fallbackCountdownSeconds} giây.");
        }

        var context = preparedContext ?? await GetPreparedMarketContextAsync(
            localDate,
            scanner.SourceSerial,
            activeRound.Value,
            scanner.LastResultId,
            analysisWindow,
            cancellationToken);
        var current = new BettingSignalSnapshotDto(
            0,
            scanner.SourceSerial ?? string.Empty,
            context.LocalDate,
            activeRound.Value,
            liveSignals?.ObservedAtUtc ?? scanner.CountdownObservedAtUtc ?? DateTimeOffset.UtcNow,
            liveSignals?.HotItemCode,
            liveSignals?.Items ?? [],
            null);
        var result = BuildMarketSignalPrediction(
            context,
            snapshots,
            current,
            housePerformance);
        var signalMessage = hasOptionalMarketSignal
            ? $"Đã phân tích ngay khi nhận được {(hasHotSignal && hasCoinSignal ? "HOT và xu" : hasHotSignal ? "HOT" : "xu")} của round {activeRound}."
            : $"Không có HOT/xu trong 10 giây đầu; đã tự chốt bằng nền 500 cầu và cầu Đỏ/Xanh của round {activeRound}.";
        return new MarketPredictionResponseDto(
            "READY",
            $"{signalMessage} Đồng hồ còn {countdownSeconds?.ToString() ?? "--"} giây.",
            activeRound,
            countdownSeconds,
            liveSignals?.ObservedAtUtc ?? scanner.CountdownObservedAtUtc,
            liveSignals?.HotItemCode,
            analysisWindow,
            matchedRounds,
            housePerformance,
            hotOutcomeStats,
            result.SideForecast,
            "CPU",
            stopwatch.ElapsedMilliseconds,
            result.Prediction);
    }

    private async Task<PredictionContextDto> GetPreparedMarketContextAsync(
        DateOnly localDate,
        string? sourceSerial,
        int roundNumber,
        long? lastResultId,
        int analysisWindow,
        CancellationToken cancellationToken)
    {
        var key = $"{localDate:yyyy-MM-dd}|{sourceSerial}|{roundNumber}|{lastResultId}";
        await _marketContextLock.WaitAsync(cancellationToken);
        try
        {
            if (_marketContextKey == key && _marketContext is not null)
            {
                return _marketContext;
            }
            var context = await _database.GetRecentPredictionContextAsync(
                localDate,
                PayoutMultipliers,
                analysisWindow,
                cancellationToken);
            _marketContextKey = key;
            _marketContext = context;
            return context;
        }
        finally
        {
            _marketContextLock.Release();
        }
    }

    public async Task<AiPredictionResponseDto> GetAiPredictionAsync(
        DateOnly localDate,
        bool forceRefresh = false,
        CancellationToken cancellationToken = default)
    {
        var settings = new AiChannelSettings(
            _configuration["Prediction:AiModel"]?.Trim() ?? "openai/gpt-oss-120b",
            _configuration["Prediction:AiFallbackModel"]?.Trim() ?? "openai/gpt-oss-20b",
            _configuration["Prediction:AiApiKey"]?.Trim(),
            _configuration["Prediction:AiBaseUrl"]?.Trim() ?? "https://api.groq.com/openai/v1",
            _configuration["Prediction:AiRoute"]?.Trim() ?? "/chat/completions",
            _configuration.GetValue("Prediction:AiTestMode", false),
            "Prediction:AiApiKey");
        return await GetConfiguredAiPredictionAsync(
            localDate,
            forceRefresh,
            settings,
            _gptCache,
            cancellationToken);
    }

    public async Task<AiPredictionResponseDto> GetCompoundPredictionAsync(
        DateOnly localDate,
        bool forceRefresh = false,
        CancellationToken cancellationToken = default)
    {
        // Compound Mini reuses the existing Groq key when a channel-specific
        // key is not supplied, so no secret needs to be duplicated.
        var compoundApiKey = _configuration["Prediction:CompoundApiKey"]?.Trim();
        if (string.IsNullOrWhiteSpace(compoundApiKey))
        {
            compoundApiKey = _configuration["Prediction:AiApiKey"]?.Trim();
        }
        var settings = new AiChannelSettings(
            _configuration["Prediction:CompoundModel"]?.Trim() ?? "groq/compound-mini",
            _configuration["Prediction:CompoundFallbackModel"]?.Trim() ?? "openai/gpt-oss-20b",
            compoundApiKey,
            _configuration["Prediction:CompoundBaseUrl"]?.Trim() ??
                _configuration["Prediction:AiBaseUrl"]?.Trim() ??
                "https://api.groq.com/openai/v1",
            _configuration["Prediction:CompoundRoute"]?.Trim() ??
                _configuration["Prediction:AiRoute"]?.Trim() ??
                "/chat/completions",
            _configuration.GetValue("Prediction:CompoundTestMode", false),
            "Prediction:CompoundApiKey hoặc Prediction:AiApiKey");
        return await GetConfiguredAiPredictionAsync(
            localDate,
            forceRefresh,
            settings,
            _compoundCache,
            cancellationToken);
    }

    private async Task<AiPredictionResponseDto> GetConfiguredAiPredictionAsync(
        DateOnly localDate,
        bool forceRefresh,
        AiChannelSettings settings,
        AiChannelCache cache,
        CancellationToken cancellationToken)
    {
        if (settings.TestMode)
        {
            var testContext = await GetContextAsync(localDate, cancellationToken);
            return BuildTestAiPrediction(testContext, settings.Model);
        }
        var apiKey = settings.ApiKey;
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            return new AiPredictionResponseDto(
                "NOT_CONFIGURED",
                false,
                settings.Model,
                null,
                $"Chưa cấu hình {settings.ApiKeyLabel}. Bảng AI này độc lập và không ảnh hưởng các dự đoán khác.");
        }

        var context = await GetContextAsync(localDate, cancellationToken);
        var cacheKey = $"{context.LocalDate}:{context.History.LastOrDefault()?.Id ?? 0}:{settings.Model}:{settings.FallbackModel}";
        if (!forceRefresh && cache.Key == cacheKey && cache.Value is not null)
        {
            return cache.Value;
        }

        await cache.Lock.WaitAsync(cancellationToken);
        try
        {
            if (!forceRefresh && cache.Key == cacheKey && cache.Value is not null)
            {
                return cache.Value;
            }

            var result = await RequestAiPredictionAsync(
                context,
                settings.Model,
                apiKey,
                settings.BaseUrl,
                settings.Route,
                cancellationToken);
            if (ShouldUseFallback(result) &&
                !string.IsNullOrWhiteSpace(settings.FallbackModel) &&
                !string.Equals(settings.Model, settings.FallbackModel, StringComparison.OrdinalIgnoreCase))
            {
                _logger.LogInformation(
                    "AI model {Model} is unavailable or rate-limited; using fast fallback {FallbackModel}",
                    settings.Model,
                    settings.FallbackModel);
                result = await RequestAiPredictionAsync(
                    context,
                    settings.FallbackModel,
                    apiKey,
                    settings.BaseUrl,
                    settings.Route,
                    cancellationToken);
            }
            if (result.Status == "READY")
            {
                cache.Key = cacheKey;
                cache.Value = result;
            }
            return result;
        }
        finally
        {
            cache.Lock.Release();
        }
    }

    private async Task<AiPredictionResponseDto> RequestAiPredictionAsync(
        PredictionContextDto context,
        string model,
        string apiKey,
        string baseUrl,
        string route,
        CancellationToken cancellationToken)
    {
        var endpointText = $"{baseUrl.TrimEnd('/')}/{route.TrimStart('/')}";
        if (!Uri.TryCreate(endpointText, UriKind.Absolute, out var endpoint) ||
            endpoint.Scheme != Uri.UriSchemeHttps)
        {
            return AiError(model, "BaseUrl hoặc Route của kênh AI không hợp lệ; bắt buộc dùng HTTPS.");
        }

        var codes = PayoutMultipliers.Keys.ToArray();
        var fullHistory = context.History.ToList();
        var eligibleHistory = fullHistory
            .Where(item => PayoutMultipliers.ContainsKey(item.ItemCode))
            .ToList();
        var segments = eligibleHistory
            .GroupBy(item => item.SourceSerial)
            .SelectMany(group => BuildContiguousSegments(group.ToList()))
            .ToList();
        var latestSource = eligibleHistory.LastOrDefault()?.SourceSerial;
        var currentSegment = latestSource is null
            ? []
            : BuildContiguousSegments(eligibleHistory
                .Where(item => item.SourceSerial == latestSource)
                .ToList()).LastOrDefault() ?? [];
        var currentCodes = currentSegment.Select(item => item.ItemCode).ToList();
        var currentCategory = currentSegment.LastOrDefault()?.Category;
        var currentStreak = CurrentCategoryStreak(currentSegment);

        var dailyHistory = fullHistory
            .GroupBy(item => item.RoundLocalDate ?? "UNKNOWN")
            .OrderBy(group => group.Key)
            .Select(group => new
            {
                Date = group.Key,
                Total = group.Count(),
                Vegetable = group.Count(item => item.Category == "VEGETABLE"),
                Meat = group.Count(item => item.Category == "MEAT"),
                Special = group.Count(item => item.Category == "SPECIAL"),
                Items = codes.ToDictionary(
                    code => code,
                    code => group.Count(item => item.ItemCode == code))
            })
            .ToList();

        var itemTransitionCounts = codes.ToDictionary(
            from => from,
            _ => codes.ToDictionary(to => to, _ => 0));
        var categoryTransitionCounts = new Dictionary<string, Dictionary<string, int>>
        {
            ["VEGETABLE"] = new() { ["VEGETABLE"] = 0, ["MEAT"] = 0 },
            ["MEAT"] = new() { ["VEGETABLE"] = 0, ["MEAT"] = 0 }
        };
        var streakSamples = new Dictionary<(string Category, int Length), (int Total, int Continued)>();
        foreach (var segment in segments)
        {
            for (var nextIndex = 1; nextIndex < segment.Count; nextIndex++)
            {
                var previous = segment[nextIndex - 1];
                var next = segment[nextIndex];
                itemTransitionCounts[previous.ItemCode][next.ItemCode]++;
                categoryTransitionCounts[previous.Category][next.Category]++;

                var length = Math.Min(20, CategoryStreakEndingAt(segment, nextIndex - 1));
                var key = (previous.Category, length);
                var sample = streakSamples.GetValueOrDefault(key);
                streakSamples[key] = (
                    sample.Total + 1,
                    sample.Continued + (next.Category == previous.Category ? 1 : 0));
            }
        }
        var streakSurvival = streakSamples
            .OrderBy(entry => entry.Key.Category)
            .ThenBy(entry => entry.Key.Length)
            .Select(entry => new
            {
                category = entry.Key.Category,
                length = entry.Key.Length,
                samples = entry.Value.Total,
                continued = entry.Value.Continued,
                continuationPercent = Math.Round(
                    100d * entry.Value.Continued / Math.Max(1, entry.Value.Total), 2)
            })
            .ToList();

        var ngramEvidence = new List<object>();
        for (var depth = 1; depth <= Math.Min(8, currentCodes.Count); depth++)
        {
            var suffix = currentCodes.TakeLast(depth).ToArray();
            var nextCounts = codes.ToDictionary(code => code, _ => 0);
            var matches = 0;
            foreach (var segment in segments)
            {
                for (var nextIndex = depth; nextIndex < segment.Count; nextIndex++)
                {
                    var matched = true;
                    for (var offset = 0; offset < depth; offset++)
                    {
                        if (segment[nextIndex - depth + offset].ItemCode != suffix[offset])
                        {
                            matched = false;
                            break;
                        }
                    }
                    if (!matched)
                    {
                        continue;
                    }
                    nextCounts[segment[nextIndex].ItemCode]++;
                    matches++;
                }
            }
            ngramEvidence.Add(new
            {
                depth,
                suffix = string.Concat(suffix.Select(code => CompactItemCodes[code])),
                matches,
                nextCounts = nextCounts
                    .Where(entry => entry.Value > 0)
                    .ToDictionary(entry => entry.Key, entry => entry.Value)
            });
        }

        var recentWindows = new[] { 32, 128, 512 }
            .Select(window => new
            {
                window,
                actual = Math.Min(window, eligibleHistory.Count),
                counts = codes.ToDictionary(
                    code => code,
                    code => eligibleHistory.TakeLast(window).Count(item => item.ItemCode == code))
            })
            .ToList();
        var localBaseline = BuildHeuristic(context);
        var isCompoundModel = model.StartsWith(
            "groq/compound", StringComparison.OrdinalIgnoreCase);
        var requestData = new
        {
            v = "full-db-statistics-v3",
            objective = "next regular item and VEGETABLE/MEAT category",
            date = context.LocalDate,
            payout = PayoutMultipliers,
            full = new
            {
                analyzedRecords = fullHistory.Count,
                predictableRecords = eligibleHistory.Count,
                analyzedDays = dailyHistory.Count,
                sourceCount = fullHistory.Select(item => item.SourceSerial).Distinct().Count(),
                daily = dailyHistory,
                itemTransitions = itemTransitionCounts,
                categoryTransitions = categoryTransitionCounts,
                streakSurvival
            },
            current = new
            {
                source = latestSource,
                category = currentCategory,
                streak = currentStreak,
                latest = string.Concat(currentCodes
                    .TakeLast(16)
                    .Select(code => CompactItemCodes[code])),
                ngrams = ngramEvidence,
                recent = recentWindows,
                today = context.TodayCounts
            },
            baseline = localBaseline.Items.Select(item => new
            {
                code = item.ItemCode,
                p = item.ProbabilityPercent,
                score = item.SignalScore,
                matches = item.PatternMatches
            })
        };
        var systemPrompt = isCompoundModel
            ? """
                Fast statistical ensemble; use only the supplied full-database feature summary and do not call tools.
                The backend has already calculated every daily count, transition, n-gram, streak-hazard and recency window
                from all database rows. analyzedRecords=full.analyzedRecords; analyzedDays=full.analyzedDays.
                Combine empirical Bayes, Markov, n-gram, streak hazard, recency drift and baseline agreement.
                algorithmsUsed has >=4 methods. Return eight unique items:
                CA_ROT,NGO,CAI,CA_CHUA,BANH_MI,XIEN,DUI,BO. Item probabilities sum 100; vegetable/meat sums match
                their four items and total 100. Use very short Vietnamese numeric reasons. Output ONLY one JSON object with
                keys analyzedRecords,analyzedDays,confidencePercent,vegetableProbabilityPercent,
                meatProbabilityPercent,algorithmsUsed,methodNote,items; each item has itemCode,probabilityPercent,
                reason. No wrapper, prose or Markdown.
                """
            : """
                Statistical sequence forecast using only the supplied full-database feature summary. The backend has
                already calculated daily frequencies, item/category Markov transitions, n-grams 1-8, streak survival,
                recency windows and a calibrated baseline from EVERY database row and every 23:00-23:00 business day.
                Combine those signals; avoid gambler's fallacy. analyzedRecords MUST equal full.analyzedRecords and
                analyzedDays MUST equal full.analyzedDays. algorithmsUsed lists at least four methods actually combined.
                items must contain each of these exactly once: CA_ROT, NGO, CAI, CA_CHUA, BANH_MI, XIEN, DUI, BO.
                The 8 item probabilityPercent values must be non-negative and sum to 100.
                vegetableProbabilityPercent must equal the sum of the four vegetable items; meatProbabilityPercent
                must equal the sum of the four meat items; those two values must sum to 100.
                Write very short Vietnamese reasons tied to numeric evidence. Output only one JSON object.
                """;
        var requestPayload = new Dictionary<string, object?>
        {
            ["model"] = model,
            ["temperature"] = 0.1,
            ["max_completion_tokens"] = 1000,
            ["response_format"] = AiResponseFormat,
            ["messages"] = new object[]
            {
                new { role = "system", content = systemPrompt },
                new { role = "user", content = JsonSerializer.Serialize(requestData, JsonOptions) }
            }
        };
        // Compound systems choose and orchestrate their underlying reasoning
        // model, so Groq rejects reasoning_effort for these model IDs.
        if (!isCompoundModel)
        {
            requestPayload["reasoning_effort"] = "low";
        }

        try
        {
            var client = _httpClientFactory.CreateClient("ai-prediction-provider");
            async Task<HttpResponseMessage> SendProviderRequestAsync()
            {
                using var request = new HttpRequestMessage(HttpMethod.Post, endpoint);
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
                request.Content = JsonContent.Create(requestPayload, options: JsonOptions);
                return await client.SendAsync(request, cancellationToken);
            }

            var response = await SendProviderRequestAsync();
            try
            {
                var responseBody = await response.Content.ReadAsStringAsync(cancellationToken);
                if (!response.IsSuccessStatusCode)
                {
                    _logger.LogWarning(
                        "AI prediction API returned HTTP {StatusCode} for model {Model}: {ProviderError}",
                        (int)response.StatusCode,
                        model,
                        ExtractProviderError(responseBody));
                    return AiError(
                        model,
                        $"AI API trả về HTTP {(int)response.StatusCode}: {ExtractProviderError(responseBody)}");
                }

                var content = ExtractAssistantContent(responseBody);
                var modelOutput = ParseAiModelOutput(content);
                if (modelOutput is null)
                {
                    _logger.LogWarning(
                        "AI returned invalid JSON/schema for model {Model}: content={ModelContent}; response={ResponseBody}",
                        model,
                        Truncate(content ?? "<null>", 5000),
                        Truncate(responseBody, 5000));
                    return AiError(model, "AI trả về nội dung không đúng JSON/schema yêu cầu.");
                }
                var prediction = BuildAiPrediction(context, model, modelOutput);
                if (prediction is null)
                {
                    _logger.LogWarning(
                        "AI output failed semantic validation for model {Model}: {ModelOutput}",
                        model,
                        Truncate(content ?? string.Empty, 5000));
                    return AiError(model, "AI trả thiếu vật phẩm hoặc xác suất không hợp lệ.");
                }
                return new AiPredictionResponseDto("READY", true, model, prediction, null);
            }
            finally
            {
                response.Dispose();
            }
        }
        catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
        {
            _logger.LogWarning(exception, "AI prediction timed out for model {Model}", model);
            return AiError(model, "AI API quá thời gian phản hồi.");
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            _logger.LogWarning(exception, "AI prediction request failed for model {Model}", model);
            return AiError(model, "Không gọi được AI API. Kiểm tra API key, endpoint hoặc kết nối mạng.");
        }
    }

    private static AiPredictionResponseDto AiError(string model, string message) =>
        new("ERROR", true, model, null, message);

    private static bool ShouldUseFallback(AiPredictionResponseDto response) =>
        response.Status != "READY" &&
        (response.Message?.Contains("HTTP 413", StringComparison.OrdinalIgnoreCase) == true ||
         response.Message?.Contains("HTTP 429", StringComparison.OrdinalIgnoreCase) == true ||
         response.Message?.Contains("quá thời gian", StringComparison.OrdinalIgnoreCase) == true ||
         response.Message?.Contains("JSON/schema", StringComparison.OrdinalIgnoreCase) == true ||
         response.Message?.Contains("thiếu vật phẩm", StringComparison.OrdinalIgnoreCase) == true);

    private static string ExtractProviderError(string responseBody)
    {
        try
        {
            using var document = JsonDocument.Parse(responseBody);
            if (document.RootElement.TryGetProperty("error", out var error) &&
                error.TryGetProperty("message", out var message) &&
                message.ValueKind == JsonValueKind.String)
            {
                var detail = message.GetString() ?? "Yêu cầu không hợp lệ.";
                if (error.TryGetProperty("failed_generation", out var failedGeneration))
                {
                    detail += " | generation=" + failedGeneration.GetRawText();
                }
                return Truncate(detail, 900);
            }
        }
        catch (JsonException)
        {
            // Fall through to a bounded plain-text error.
        }
        return Truncate(string.IsNullOrWhiteSpace(responseBody)
            ? "Nhà cung cấp không trả chi tiết lỗi."
            : responseBody.Trim(), 500);
    }

    private static string Truncate(string value, int maximumLength) =>
        value.Length <= maximumLength ? value : value[..maximumLength] + "…";

    private static AiPredictionResponseDto BuildTestAiPrediction(
        PredictionContextDto context,
        string configuredModel)
    {
        var testModel = $"{configuredModel}-test-simulator";
        var baseline = BuildHeuristic(context);
        var output = new AiModelOutput(
            context.History.Count,
            context.History.Select(item => item.RoundLocalDate).Distinct().Count(),
            Math.Clamp(baseline.ModelConfidencePercent - 4, 35, 90),
            baseline.VegetableProbabilityPercent,
            baseline.MeatProbabilityPercent,
            ["full-history-frequency", "markov-transition", "ngram-1-8", "streak-survival"],
            "CHẾ ĐỘ TEST NỘI BỘ: mô phỏng response AI từ toàn bộ database để kiểm tra giao diện và luồng tích hợp; chưa gọi GPT thật.",
            baseline.Items.Select(item => new AiModelItem(
                item.ItemCode,
                null,
                Math.Pow(Math.Max(item.ProbabilityPercent, 0.01), 1.06),
                $"TEST · {item.Reason}"))
            .ToList());
        var prediction = BuildAiPrediction(context, testModel, output);
        return prediction is null
            ? AiError(testModel, "Không tạo được dữ liệu mô phỏng AI.")
            : new AiPredictionResponseDto("READY", true, testModel, prediction, null);
    }

    private static string? ExtractAssistantContent(string responseBody)
    {
        try
        {
            using var document = JsonDocument.Parse(responseBody);
            var root = document.RootElement;
            if (!root.TryGetProperty("choices", out var choices) ||
                choices.ValueKind != JsonValueKind.Array ||
                choices.GetArrayLength() == 0)
            {
                return null;
            }
            var first = choices[0];
            if (!first.TryGetProperty("message", out var message) ||
                !message.TryGetProperty("content", out var content) ||
                content.ValueKind != JsonValueKind.String)
            {
                return null;
            }
            return content.GetString();
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static AiModelOutput? ParseAiModelOutput(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            return null;
        }
        var firstBrace = content.IndexOf('{');
        var lastBrace = content.LastIndexOf('}');
        if (firstBrace < 0 || lastBrace <= firstBrace)
        {
            return null;
        }
        try
        {
            return JsonSerializer.Deserialize<AiModelOutput>(
                content[firstBrace..(lastBrace + 1)], JsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static PredictionDto? BuildAiPrediction(
        PredictionContextDto context,
        string model,
        AiModelOutput output)
    {
        var expectedDayCount = context.History
            .Select(item => item.RoundLocalDate ?? "UNKNOWN")
            .Distinct()
            .Count();
        if (output.Items is null ||
            output.AnalyzedRecords != context.History.Count ||
            output.AnalyzedDays != expectedDayCount ||
            output.AlgorithmsUsed is null ||
            output.AlgorithmsUsed.Where(value => !string.IsNullOrWhiteSpace(value))
                .Distinct(StringComparer.OrdinalIgnoreCase).Count() < 4)
        {
            return null;
        }
        var grouped = output.Items
            .Where(item => !string.IsNullOrWhiteSpace(item.EffectiveItemCode))
            .GroupBy(item => item.EffectiveItemCode.Trim().ToUpperInvariant())
            .ToDictionary(group => group.Key, group => group.ToList());
        if (PayoutMultipliers.Keys.Any(code => !grouped.TryGetValue(code, out var values) || values.Count != 1) ||
            grouped.Keys.Any(code => !PayoutMultipliers.ContainsKey(code)))
        {
            return null;
        }

        var raw = PayoutMultipliers.Keys.ToDictionary(
            code => code,
            code => grouped[code][0].ProbabilityPercent);
        if (raw.Values.Any(value => double.IsNaN(value) || double.IsInfinity(value) || value < 0) ||
            raw.Values.Sum() <= 0)
        {
            return null;
        }
        var codes = PayoutMultipliers.Keys.ToArray();
        var normalizedAi = Normalize(raw, codes);
        var reportedVegetable = Math.Clamp(output.VegetableProbabilityPercent, 0, 100);
        var reportedMeat = Math.Clamp(output.MeatProbabilityPercent, 0, 100);
        var itemVegetable = codes
            .Where(code => ItemMeta[code].Category == "VEGETABLE")
            .Sum(code => normalizedAi[code]) * 100;
        if (Math.Abs(reportedVegetable + reportedMeat - 100) > 1.5 ||
            Math.Abs(reportedVegetable - itemVegetable) > 2.0)
        {
            return null;
        }

        // Keep the external model as the main signal, but calibrate it against
        // the deterministic full-database baseline so one hallucinated score
        // cannot dominate the published probabilities.
        var baseline = BuildHeuristic(context);
        var normalizedBaseline = Normalize(
            baseline.Items.ToDictionary(
                item => item.ItemCode,
                item => Math.Max(0.000001, item.ProbabilityPercent / 100d)),
            codes);
        var modelSelfConfidence = Math.Clamp(output.ConfidencePercent, 0, 100);
        var agreement = Math.Clamp(
            1d - 0.5 * codes.Sum(code =>
                Math.Abs(normalizedAi[code] - normalizedBaseline[code])),
            0,
            1);
        var confidence = Math.Clamp(
            0.35 * modelSelfConfidence +
            0.35 * baseline.ModelConfidencePercent +
            0.30 * agreement * 100,
            15,
            94);
        var aiWeight = 0.55 + 0.25 * confidence / 100d;
        var blended = codes.ToDictionary(
            code => code,
            code => aiWeight * normalizedAi[code] +
                    (1 - aiWeight) * normalizedBaseline[code]);
        var normalized = Normalize(blended, codes);
        var rankedCodes = codes.OrderByDescending(code => normalized[code]).ToList();
        var topProbability = normalized[rankedCodes[0]];
        var minimumProbability = normalized[rankedCodes[^1]];
        var spread = Math.Max(0.000001, topProbability - minimumProbability);
        var baselineByCode = baseline.Items.ToDictionary(item => item.ItemCode);
        var items = rankedCodes.Select(code =>
        {
            var relative = (normalized[code] - minimumProbability) / spread;
            var signalScore = Math.Clamp(
                12 + 82 * Math.Pow(relative, 3.2) + 5 * confidence / 100,
                5,
                99);
            var modelItem = grouped[code][0];
            return new PredictionItemDto(
                code,
                ItemMeta[code].Name,
                ItemMeta[code].Category,
                PayoutMultipliers[code],
                context.HistoricalCounts.GetValueOrDefault(code),
                context.TodayCounts.GetValueOrDefault(code),
                Math.Round(normalized[code] * 100, 2),
                Math.Round(signalScore, 1),
                baselineByCode[code].PatternMatches,
                string.IsNullOrWhiteSpace(modelItem.Reason)
                    ? "AI xếp hạng từ mẫu chuỗi lịch sử được cung cấp."
                    : modelItem.Reason.Trim());
        }).ToList();
        var vegetableProbability = normalized
            .Where(entry => ItemMeta[entry.Key].Category == "VEGETABLE")
            .Sum(entry => entry.Value) * 100;
        var meatProbability = 100 - vegetableProbability;
        var algorithms = string.Join(", ", output.AlgorithmsUsed
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.OrdinalIgnoreCase));
        var methodNote = string.IsNullOrWhiteSpace(output.MethodNote)
            ? $"AI {model} phân tích dữ liệu lịch sử tách biệt với local-full-db-v3."
            : output.MethodNote.Trim();
        return new PredictionDto(
            $"ai-{model}",
            context.LocalDate,
            DateTimeOffset.UtcNow,
            items,
            $"{methodNote} Đã xác nhận AI đọc {output.AnalyzedRecords} bản ghi thuộc {output.AnalyzedDays} ngày; thuật toán: {algorithms}. Độ tin cậy ensemble kết hợp tự đánh giá Groq {modelSelfConfidence:F1}%, độ tin cậy local {baseline.ModelConfidencePercent:F1}% và mức đồng thuận hai mô hình {agreement * 100:F1}%. Kết quả được chuẩn hóa về 100%; đây là ước lượng thống kê, không bảo đảm thắng.",
            Math.Round(confidence, 1),
            Math.Round(vegetableProbability, 2),
            Math.Round(meatProbability, 2),
            rankedCodes[0]);
    }

    private sealed record AiModelOutput(
        int AnalyzedRecords,
        int AnalyzedDays,
        double ConfidencePercent,
        double VegetableProbabilityPercent,
        double MeatProbabilityPercent,
        IReadOnlyList<string>? AlgorithmsUsed,
        string? MethodNote,
        IReadOnlyList<AiModelItem>? Items);

    private sealed record AiModelItem(
        string? ItemCode,
        string? Code,
        double ProbabilityPercent,
        string? Reason)
    {
        public string EffectiveItemCode =>
            !string.IsNullOrWhiteSpace(ItemCode) ? ItemCode : Code ?? string.Empty;
    }

    private sealed record AiChannelSettings(
        string Model,
        string? FallbackModel,
        string? ApiKey,
        string BaseUrl,
        string Route,
        bool TestMode,
        string ApiKeyLabel);

    private sealed class AiChannelCache
    {
        public SemaphoreSlim Lock { get; } = new(1, 1);
        public string? Key { get; set; }
        public AiPredictionResponseDto? Value { get; set; }
    }

    private static PredictionDto BuildHeuristic(PredictionContextDto context)
    {
        var codes = PayoutMultipliers.Keys.ToArray();
        var baseWeights = codes.ToDictionary(code => code, code => 1d / PayoutMultipliers[code]);
        var baseDistribution = Normalize(baseWeights, codes);
        var activeHistory = context.History
            .Where(item => PayoutMultipliers.ContainsKey(item.ItemCode))
            .ToList();
        var segments = activeHistory
            .GroupBy(item => item.SourceSerial)
            .SelectMany(group => BuildContiguousSegments(group.ToList()))
            .ToList();
        var latestSource = activeHistory.LastOrDefault()?.SourceSerial;
        var currentSegment = latestSource is null
            ? []
            : BuildContiguousSegments(activeHistory
                .Where(item => item.SourceSerial == latestSource)
                .ToList()).LastOrDefault() ?? [];
        var sourceCounts = codes.ToDictionary(
            code => code,
            code => (double)segments.Sum(segment => segment.Count(item => item.ItemCode == code)));
        var historical = Normalize(sourceCounts, codes, baseDistribution);
        var today = Normalize(
            codes.ToDictionary(code => code, code => (double)context.TodayCounts.GetValueOrDefault(code)),
            codes,
            historical);

        var recentWeights = codes.ToDictionary(code => code, _ => 0d);
        var recentItems = activeHistory;
        for (var index = 0; index < recentItems.Count; index++)
        {
            var age = recentItems.Count - 1 - index;
            recentWeights[recentItems[index].ItemCode] += Math.Pow(0.982, age);
        }
        var recent = Normalize(recentWeights, codes, historical);

        var patternWeights = codes.ToDictionary(code => code, _ => 0d);
        var itemPatternMatches = codes.ToDictionary(code => code, _ => 0);
        var currentCodes = currentSegment.Select(item => item.ItemCode).ToList();
        var strongestDepth = 0;
        var strongestMatches = 0;
        var totalPatternMatches = 0;
        var patternComponentWeight = 0d;
        for (var depth = Math.Min(8, currentCodes.Count); depth >= 1; depth--)
        {
            var suffix = currentCodes.TakeLast(depth).ToArray();
            var nextCounts = codes.ToDictionary(code => code, _ => 0d);
            var matches = 0;
            foreach (var segment in segments)
            {
                for (var nextIndex = depth; nextIndex < segment.Count; nextIndex++)
                {
                    var matched = true;
                    for (var offset = 0; offset < depth; offset++)
                    {
                        if (segment[nextIndex - depth + offset].ItemCode != suffix[offset])
                        {
                            matched = false;
                            break;
                        }
                    }
                    if (!matched)
                    {
                        continue;
                    }
                    var nextCode = segment[nextIndex].ItemCode;
                    nextCounts[nextCode]++;
                    itemPatternMatches[nextCode]++;
                    matches++;
                }
            }
            if (matches == 0)
            {
                continue;
            }
            if (strongestDepth == 0)
            {
                strongestDepth = depth;
                strongestMatches = matches;
            }
            totalPatternMatches += matches;
            var component = Normalize(nextCounts, codes, historical);
            var componentWeight = depth * depth * Math.Log2(matches + 2);
            foreach (var code in codes)
            {
                patternWeights[code] += componentWeight * component[code];
            }
            patternComponentWeight += componentWeight;
        }
        var pattern = patternComponentWeight > 0
            ? Normalize(patternWeights, codes, historical)
            : historical;

        var streakWeights = codes.ToDictionary(code => code, _ => 0d);
        var currentCategory = currentSegment.LastOrDefault()?.Category;
        var currentStreak = CurrentCategoryStreak(currentSegment);
        var streakSamples = 0d;
        if (currentCategory is not null)
        {
            foreach (var segment in segments)
            {
                for (var nextIndex = 1; nextIndex < segment.Count; nextIndex++)
                {
                    if (segment[nextIndex - 1].Category != currentCategory)
                    {
                        continue;
                    }
                    var historicalStreak = CategoryStreakEndingAt(segment, nextIndex - 1);
                    var distance = Math.Abs(Math.Min(12, historicalStreak) - Math.Min(12, currentStreak));
                    var weight = 1d / (1d + distance);
                    streakWeights[segment[nextIndex].ItemCode] += weight;
                    streakSamples += weight;
                }
            }
        }
        var streak = streakSamples > 0
            ? Normalize(streakWeights, codes, historical)
            : historical;

        var scores = codes.ToDictionary(code => code, code =>
            0.05 * baseDistribution[code] +
            0.13 * historical[code] +
            0.10 * today[code] +
            0.12 * recent[code] +
            0.45 * pattern[code] +
            0.15 * streak[code]);

        // A lower temperature separates candidates that have stronger conditional
        // evidence while the final probabilities still add up to exactly 100%.
        var sharpened = codes.ToDictionary(
            code => code,
            code => Math.Pow(Math.Max(scores[code], 0.000001), 1d / 0.58));
        var normalized = Normalize(sharpened, codes);
        var rankedCodes = codes.OrderByDescending(code => normalized[code]).ToList();
        var topProbability = normalized[rankedCodes[0]];
        var secondProbability = normalized[rankedCodes[1]];
        var minimumProbability = normalized[rankedCodes[^1]];
        var sampleConfidence = 1d - Math.Exp(-strongestMatches / 4d);
        var backoffConfidence = 1d - Math.Exp(-totalPatternMatches / 60d);
        var modelConfidence = Math.Clamp(
            42d + 4.5 * strongestDepth + 14 * sampleConfidence + 10 * backoffConfidence +
            Math.Min(15, (topProbability - secondProbability) * 180),
            40,
            94);
        var maximumPatternMatches = Math.Max(1, itemPatternMatches.Values.Max());
        var probabilitySpread = Math.Max(0.000001, topProbability - minimumProbability);

        var items = rankedCodes.Select(code =>
        {
            var relativeProbability = (normalized[code] - minimumProbability) / probabilitySpread;
            var relativePattern = itemPatternMatches[code] / (double)maximumPatternMatches;
            var signalScore = Math.Clamp(
                12 + 76 * Math.Pow(relativeProbability, 3.2) + 7 * Math.Sqrt(relativePattern) +
                5 * modelConfidence / 100,
                5,
                99);
            var reason = itemPatternMatches[code] > 0
                ? $"Theo {itemPatternMatches[code]} lần chuyển tiếp sau mẫu tương tự; mẫu khớp sâu nhất {strongestDepth} cầu."
                : currentCategory == ItemMeta[code].Category
                    ? $"Phù hợp nhịp {CategoryLabel(currentCategory)} đang chạy {currentStreak} cầu và tần suất gần đây."
                    : "Tổng hợp toàn bộ database theo ngày, tần suất lịch sử và trọng số độ mới.";
            return new PredictionItemDto(
                code,
                ItemMeta[code].Name,
                ItemMeta[code].Category,
                PayoutMultipliers[code],
                context.HistoricalCounts.GetValueOrDefault(code),
                context.TodayCounts.GetValueOrDefault(code),
                Math.Round(normalized[code] * 100, 2),
                Math.Round(signalScore, 1),
                itemPatternMatches[code],
                reason);
        }).ToList();
        var vegetableProbability = normalized
            .Where(entry => ItemMeta[entry.Key].Category == "VEGETABLE")
            .Sum(entry => entry.Value) * 100;
        var meatProbability = 100 - vegetableProbability;

        return new PredictionDto(
            "local-full-db-v3",
            context.LocalDate,
            DateTimeOffset.UtcNow,
            items,
            $"Phân tích {activeHistory.Count} bản ghi trong toàn bộ database, tách chuỗi theo ngày kèo 23:00–23:00 và nguồn máy; khớp n-gram 1–8 cầu, chuyển tiếp bệt hiện tại {currentStreak} cầu và trọng số độ mới trên toàn bộ lịch sử. Xác suất cộng đủ 100%; chỉ số dự đoán 0–100 là độ nổi bật tương đối đã tách hạng, không phải xác suất thắng. Đã tìm {totalPatternMatches} chuyển tiếp mẫu.",
            Math.Round(modelConfidence, 1),
            Math.Round(vegetableProbability, 2),
            Math.Round(meatProbability, 2),
            rankedCodes[0]);
    }

    private static MarketPredictionBuildResult BuildMarketSignalPrediction(
        PredictionContextDto context,
        IReadOnlyList<BettingSignalSnapshotDto> snapshots,
        BettingSignalSnapshotDto current,
        HousePerformanceDto housePerformance)
    {
        // This model is intentionally separate from BuildHeuristic. The old
        // prediction remains stable while this channel learns whether HOT and
        // crowd-coin observations have any measurable relationship to results.
        var baseline = BuildHeuristic(context);
        var codes = PayoutMultipliers.Keys.ToArray();
        var baselineDistribution = baseline.Items.ToDictionary(
            item => item.ItemCode,
            item => Math.Max(0.000001, item.ProbabilityPercent / 100d));
        var completed = snapshots
            .Where(snapshot => snapshot.ResultItemCode is not null &&
                               PayoutMultipliers.ContainsKey(snapshot.ResultItemCode))
            .TakeLast(500)
            .ToList();
        var currentActivity = SignalActivity(current, codes);
        var outcomeWeights = codes.ToDictionary(code => code, _ => 0d);
        var outcomeSamples = codes.ToDictionary(code => code, _ => 0);
        var effectiveSamples = 0d;
        var matchingHotSamples = current.HotItemCode is null
            ? []
            : completed.Where(snapshot => snapshot.HotItemCode == current.HotItemCode).ToList();

        for (var index = 0; index < completed.Count; index++)
        {
            var snapshot = completed[index];
            var resultCode = snapshot.ResultItemCode!;
            var observed = SignalActivity(snapshot, codes);
            var similarity = codes.Average(code => 1d - Math.Min(1d, Math.Abs(
                observed.GetValueOrDefault(code) - currentActivity.GetValueOrDefault(code)) / 100d));
            if (current.HotItemCode is not null)
            {
                similarity *= snapshot.HotItemCode == current.HotItemCode ? 1.35 : 0.72;
            }
            var age = completed.Count - 1 - index;
            var weight = Math.Max(0.05, similarity) * Math.Pow(0.995, age);
            outcomeWeights[resultCode] += weight;
            outcomeSamples[resultCode]++;
            effectiveSamples += weight;
        }

        // Bayesian shrinkage prevents a few early HOT/coin samples from
        // overpowering the long-running model.
        const double priorStrength = 24d;
        var empirical = Normalize(
            codes.ToDictionary(
                code => code,
                code => outcomeWeights[code] + priorStrength * baselineDistribution[code]),
            codes,
            baselineDistribution);

        // Learn explicit transitions such as "HOT Xiên -> Bò". A separate,
        // strongly-shrunk conditional distribution makes that relationship
        // visible to the model without letting a handful of rounds dominate.
        const double hotPriorStrength = 12d;
        var hotOutcomeCounts = codes.ToDictionary(
            code => code,
            code => matchingHotSamples.Count(snapshot => snapshot.ResultItemCode == code));
        var hotConditional = Normalize(
            codes.ToDictionary(
                code => code,
                code => hotOutcomeCounts[code] + hotPriorStrength * baselineDistribution[code]),
            codes,
            baselineDistribution);

        // Exposure is only a weak, testable feature. It does not assume that a
        // provider manipulates outcomes; historical calibration above decides
        // whether crowd activity has predictive value.
        var exposure = Normalize(
            codes.ToDictionary(code => code, code =>
            {
                var activity = currentActivity.GetValueOrDefault(code) / 100d;
                var liability = activity * PayoutMultipliers[code];
                return baselineDistribution[code] / (1d + liability);
            }),
            codes,
            baselineDistribution);

        var evidence = 1d - Math.Exp(-effectiveSamples / 35d);
        var empiricalWeight = Math.Min(0.30, evidence * 0.30);
        var hotEvidence = 1d - Math.Exp(-matchingHotSamples.Count / 18d);
        var hotWeight = current.HotItemCode is null ? 0d : Math.Min(0.22, hotEvidence * 0.22);
        var measuredRiskBias = Math.Clamp(
            (housePerformance.RiskAvoidanceScorePercent - 50d) / 50d,
            0d,
            1d);
        var exposureWeight = Math.Min(0.14, evidence * measuredRiskBias * 0.14);
        var baselineWeight = 1d - empiricalWeight - hotWeight - exposureWeight;
        var combined = Normalize(
            codes.ToDictionary(code => code, code =>
                baselineWeight * baselineDistribution[code] +
                empiricalWeight * empirical[code] +
                hotWeight * hotConditional[code] +
                exposureWeight * exposure[code]),
            codes,
            baselineDistribution);
        var sideSignal = AnalyzeMarketSides(context, combined);
        var redProbabilityBeforeSideSignal = codes
            .Where(RedSideCodes.Contains)
            .Sum(code => combined[code]);
        var greenProbabilityBeforeSideSignal = 1d - redProbabilityBeforeSideSignal;
        foreach (var code in codes)
        {
            combined[code] *= RedSideCodes.Contains(code)
                ? sideSignal.RedProbability / Math.Max(0.000001, redProbabilityBeforeSideSignal)
                : sideSignal.GreenProbability / Math.Max(0.000001, greenProbabilityBeforeSideSignal);
        }
        combined = Normalize(combined, codes, baselineDistribution);
        var probabilityRankedCodes = codes.OrderByDescending(code => combined[code]).ToList();
        var valueIndex = codes.ToDictionary(
            code => code,
            code => combined[code] * PayoutMultipliers[code]);
        var rankedCodes = codes.OrderByDescending(code => valueIndex[code]).ToList();
        var spread = combined[probabilityRankedCodes[0]] - combined[probabilityRankedCodes[1]];
        var modelConfidence = Math.Clamp(
            30d + 35d * evidence + Math.Min(15d, spread * 180d),
            30d,
            82d);
        var maxValue = valueIndex.Values.Max();
        var minValue = valueIndex.Values.Min();
        var valueSpread = Math.Max(0.000001, maxValue - minValue);
        var hotLabel = current.HotItemCode is { } hot && ItemMeta.TryGetValue(hot, out var hotMeta)
            ? hotMeta.Name
            : "chưa nhận diện";

        var items = rankedCodes.Select(code =>
        {
            var relative = (valueIndex[code] - minValue) / valueSpread;
            var activity = currentActivity.GetValueOrDefault(code);
            var isHot = current.HotItemCode == code;
            var liability = activity / 100d * PayoutMultipliers[code];
            var colorSide = RedSideCodes.Contains(code) ? "Đỏ" : "Xanh";
            var hotEvidenceNote = current.HotItemCode is null
                ? "không có HOT hiện tại"
                : $"HOT này đã ghi {matchingHotSamples.Count} cầu, về {ItemMeta[code].Name} {hotOutcomeCounts[code]} lần";
            var reason = $"{(isHot ? "Đang HOT" : "Không HOT")}, thuộc bên {colorSide}, mức xu {activity:0}%, chỉ số hoàn trả kỳ vọng {valueIndex[code]:0.000}; nghĩa vụ trả thưởng tương đối {liability:0.00}. {hotEvidenceNote}; đối chiếu {outcomeSamples[code]} kết quả trong cửa sổ gần nhất.";
            return new PredictionItemDto(
                code,
                ItemMeta[code].Name,
                ItemMeta[code].Category,
                PayoutMultipliers[code],
                context.HistoricalCounts.GetValueOrDefault(code),
                context.TodayCounts.GetValueOrDefault(code),
                Math.Round(combined[code] * 100, 2),
                Math.Round(Math.Clamp(15 + 80 * relative, 5, 99), 1),
                outcomeSamples[code],
                reason);
        }).ToList();
        var vegetableProbability = combined
            .Where(entry => ItemMeta[entry.Key].Category == "VEGETABLE")
            .Sum(entry => entry.Value) * 100;

        var sideForecast = new MarketSideForecastDto(
            Math.Round(codes.Where(RedSideCodes.Contains).Sum(code => combined[code]) * 100, 2),
            Math.Round(codes.Where(code => !RedSideCodes.Contains(code)).Sum(code => combined[code]) * 100, 2),
            sideSignal.CurrentSide,
            sideSignal.CurrentStreak,
            sideSignal.MatchedTransitions,
            Math.Round(sideSignal.SignalWeight * 100, 1));
        var prediction = new PredictionDto(
            "market-signals-v2-color-sides",
            context.LocalDate,
            DateTimeOffset.UtcNow,
            items,
            $"Chỉ phân tích tối đa 500 cầu gần nhất. Mô hình độc lập kết hợp chuỗi kết quả, cầu Đỏ/Xanh, chuyển tiếp HOT→kết quả ({hotLabel}: {matchingHotSamples.Count} mẫu), mức xu, hệ số trả thưởng và mức độ kết quả lịch sử trùng với giả thuyết giảm nghĩa vụ nhà cái ({housePerformance.RiskAvoidanceScorePercent:0.0}%). Nhóm Đỏ gồm Cà chua, Bánh mì, Cà rốt, Bò; nhóm Xanh gồm Ngô, Cải, Xiên, Đùi. Có {sideSignal.MatchedTransitions} chuyển tiếp Đỏ/Xanh tương tự và {completed.Count} cầu ghép được tín hiệu + kết quả; cỡ mẫu HOT/xu hiệu dụng {effectiveSamples:0.0}. Trọng số chuyển tiếp HOT {hotWeight:P0}, cầu màu {sideSignal.SignalWeight:P0}, dữ liệu thị trường khác {empiricalWeight + exposureWeight:P0}. Lãi/lỗ nhà cái chỉ là đơn vị ước tính từ cấp xu quan sát, không phải tiền thật. Không bảo đảm thắng.",
            Math.Round(modelConfidence, 1),
            Math.Round(vegetableProbability, 2),
            Math.Round(100 - vegetableProbability, 2),
            rankedCodes[0]);
        return new MarketPredictionBuildResult(prediction, sideForecast);
    }

    private static MarketSideSignal AnalyzeMarketSides(
        PredictionContextDto context,
        IReadOnlyDictionary<string, double> priorDistribution)
    {
        var segments = BuildContiguousSegments(context.History
            .Where(item => PayoutMultipliers.ContainsKey(item.ItemCode))
            .ToList());
        var currentSegment = segments.LastOrDefault() ?? [];
        var currentSides = currentSegment
            .Select(item => RedSideCodes.Contains(item.ItemCode) ? "RED" : "GREEN")
            .ToList();
        var currentSide = currentSides.LastOrDefault();
        var currentStreak = 0;
        for (var index = currentSides.Count - 1; index >= 0 && currentSides[index] == currentSide; index--)
        {
            currentStreak++;
        }

        var redWeight = 0d;
        var greenWeight = 0d;
        var matchedTransitions = 0;
        var maximumDepth = Math.Min(6, currentSides.Count);
        for (var depth = maximumDepth; depth >= 1; depth--)
        {
            var suffix = currentSides.TakeLast(depth).ToArray();
            foreach (var segment in segments)
            {
                var sides = segment
                    .Select(item => RedSideCodes.Contains(item.ItemCode) ? "RED" : "GREEN")
                    .ToList();
                for (var nextIndex = depth; nextIndex < sides.Count; nextIndex++)
                {
                    var matches = true;
                    for (var offset = 0; offset < depth; offset++)
                    {
                        if (sides[nextIndex - depth + offset] != suffix[offset])
                        {
                            matches = false;
                            break;
                        }
                    }
                    if (!matches) continue;
                    var weight = depth * depth;
                    if (sides[nextIndex] == "RED") redWeight += weight;
                    else greenWeight += weight;
                    matchedTransitions++;
                }
            }
        }

        if (currentSide is not null)
        {
            foreach (var segment in segments)
            {
                var sides = segment
                    .Select(item => RedSideCodes.Contains(item.ItemCode) ? "RED" : "GREEN")
                    .ToList();
                for (var nextIndex = 1; nextIndex < sides.Count; nextIndex++)
                {
                    if (sides[nextIndex - 1] != currentSide) continue;
                    var historicalStreak = 1;
                    for (var index = nextIndex - 2; index >= 0 && sides[index] == currentSide; index--)
                    {
                        historicalStreak++;
                    }
                    var weight = 0.75 / (1d + Math.Abs(
                        Math.Min(10, historicalStreak) - Math.Min(10, currentStreak)));
                    if (sides[nextIndex] == "RED") redWeight += weight;
                    else greenWeight += weight;
                }
            }
        }

        var priorRed = priorDistribution
            .Where(entry => RedSideCodes.Contains(entry.Key))
            .Sum(entry => entry.Value);
        const double priorStrength = 16d;
        var sampleWeight = redWeight + greenWeight;
        var conditionalRed = (redWeight + priorStrength * priorRed) /
            Math.Max(0.000001, sampleWeight + priorStrength);
        var evidence = 1d - Math.Exp(-sampleWeight / 80d);
        var signalWeight = Math.Min(0.24, evidence * 0.24);
        var redProbability = Math.Clamp(
            (1d - signalWeight) * priorRed + signalWeight * conditionalRed,
            0.05,
            0.95);
        return new MarketSideSignal(
            redProbability,
            1d - redProbability,
            currentSide,
            currentStreak,
            matchedTransitions,
            signalWeight);
    }

    private static HousePerformanceDto AnalyzeHousePerformance(
        IReadOnlyList<BettingSignalSnapshotDto> snapshots,
        BettingSignalsDto? liveSignals)
    {
        var codes = PayoutMultipliers.Keys.ToArray();
        var completed = snapshots
            .Where(snapshot => snapshot.ResultItemCode is not null &&
                               PayoutMultipliers.ContainsKey(snapshot.ResultItemCode))
            .TakeLast(500)
            .ToList();
        var totalStake = 0d;
        var totalPayout = 0d;
        var houseWinningRounds = 0;
        var houseLosingRounds = 0;
        var hotObservedRounds = 0;
        var hotHitRounds = 0;
        var protectionDeltas = new List<double>();
        var theoreticalTotal = codes.Sum(code => 1d / PayoutMultipliers[code]);
        var theoreticalProbability = codes.ToDictionary(
            code => code,
            code => (1d / PayoutMultipliers[code]) / theoreticalTotal);

        foreach (var snapshot in completed)
        {
            var coins = snapshot.Items
                .Where(item => PayoutMultipliers.ContainsKey(item.ItemCode))
                .GroupBy(item => item.ItemCode)
                .ToDictionary(group => group.Key, group => Math.Max(0, group.Max(item => item.CoinCount)));
            var stake = codes.Sum(code => coins.GetValueOrDefault(code));
            if (stake <= 0) continue;
            var resultCode = snapshot.ResultItemCode!;
            var payout = coins.GetValueOrDefault(resultCode) * PayoutMultipliers[resultCode];
            var net = stake - payout;
            totalStake += stake;
            totalPayout += payout;
            if (net >= 0) houseWinningRounds++;
            else houseLosingRounds++;

            if (!string.IsNullOrWhiteSpace(snapshot.HotItemCode))
            {
                hotObservedRounds++;
                if (snapshot.HotItemCode == resultCode) hotHitRounds++;
            }

            var liabilities = codes
                .Select(code => (Code: code, Value: coins.GetValueOrDefault(code) * PayoutMultipliers[code]))
                .OrderBy(entry => entry.Value)
                .ToList();
            var resultIndex = liabilities.FindIndex(entry => entry.Code == resultCode);
            if (resultIndex >= 0)
            {
                var protectionByCode = liabilities
                    .Select((entry, index) => new
                    {
                        entry.Code,
                        Score = 1d - index / (double)Math.Max(1, liabilities.Count - 1)
                    })
                    .ToDictionary(entry => entry.Code, entry => entry.Score);
                var observedProtection = protectionByCode[resultCode];
                var neutralProtection = codes.Sum(code =>
                    theoreticalProbability[code] * protectionByCode[code]);
                protectionDeltas.Add(observedProtection - neutralProtection);
            }
        }

        var liveCoins = liveSignals?.Items
            .Where(item => PayoutMultipliers.ContainsKey(item.ItemCode))
            .GroupBy(item => item.ItemCode)
            .ToDictionary(group => group.Key, group => Math.Max(0, group.Max(item => item.CoinCount))) ?? [];
        var liveLiabilities = codes.ToDictionary(
            code => code,
            code => liveCoins.GetValueOrDefault(code) * PayoutMultipliers[code]);
        var highestLiability = liveLiabilities.OrderByDescending(entry => entry.Value).FirstOrDefault();
        var currentStake = codes.Sum(code => (double)liveCoins.GetValueOrDefault(code));
        var currentScenarios = codes
            .Select(code =>
            {
                var coinLevel = liveCoins.GetValueOrDefault(code);
                var payout = coinLevel * PayoutMultipliers[code];
                return new HouseOutcomeScenarioDto(
                    code,
                    coinLevel,
                    Math.Round(payout, 2),
                    Math.Round(currentStake - payout, 2));
            })
            .OrderBy(scenario => scenario.EstimatedHouseNetUnits)
            .ToList();
        var netUnits = totalStake - totalPayout;

        return new HousePerformanceDto(
            houseWinningRounds + houseLosingRounds,
            Math.Round(totalStake, 2),
            Math.Round(totalPayout, 2),
            Math.Round(netUnits, 2),
            totalStake <= 0 ? 0 : Math.Round(netUnits * 100d / totalStake, 2),
            houseWinningRounds,
            houseLosingRounds,
            hotObservedRounds,
            hotObservedRounds == 0 ? 0 : Math.Round(hotHitRounds * 100d / hotObservedRounds, 2),
            protectionDeltas.Count == 0
                ? 50
                : Math.Round(Math.Clamp(50d + 50d * protectionDeltas.Average(), 0d, 100d), 2),
            highestLiability.Value > 0 ? highestLiability.Key : null,
            Math.Round(highestLiability.Value, 2),
            Math.Round(currentStake, 2),
            currentScenarios);
    }

    private static IReadOnlyList<HotOutcomeStatDto> AnalyzeHotOutcomes(
        IReadOnlyList<BettingSignalSnapshotDto> snapshots,
        string? currentHotItemCode)
    {
        if (string.IsNullOrWhiteSpace(currentHotItemCode)) return [];
        var matching = snapshots
            .Where(snapshot => snapshot.HotItemCode == currentHotItemCode &&
                               snapshot.ResultItemCode is not null &&
                               PayoutMultipliers.ContainsKey(snapshot.ResultItemCode))
            .TakeLast(500)
            .ToList();
        return PayoutMultipliers.Keys
            .Select(code =>
            {
                var count = matching.Count(snapshot => snapshot.ResultItemCode == code);
                return new HotOutcomeStatDto(
                    currentHotItemCode,
                    code,
                    matching.Count,
                    count,
                    matching.Count == 0 ? 0 : Math.Round(count * 100d / matching.Count, 2));
            })
            .OrderByDescending(stat => stat.OutcomeRounds)
            .ThenBy(stat => stat.OutcomeItemCode)
            .ToList();
    }

    private static Dictionary<string, double> SignalActivity(
        BettingSignalSnapshotDto? snapshot,
        IReadOnlyList<string> codes)
    {
        var values = codes.ToDictionary(code => code, _ => 0d);
        if (snapshot is null) return values;
        foreach (var item in snapshot.Items.Where(item => values.ContainsKey(item.ItemCode)))
        {
            values[item.ItemCode] = Math.Clamp(item.ActivityPercent, 0, 100);
        }
        return values;
    }

    private static List<List<PredictionHistoryItemDto>> BuildContiguousSegments(
        IReadOnlyList<PredictionHistoryItemDto> history)
    {
        var segments = new List<List<PredictionHistoryItemDto>>();
        List<PredictionHistoryItemDto>? current = null;
        PredictionHistoryItemDto? previous = null;
        foreach (var item in history)
        {
            if (!PayoutMultipliers.ContainsKey(item.ItemCode))
            {
                current = null;
                previous = null;
                continue;
            }
            if (current is null || previous is null || !AreContiguous(previous, item))
            {
                current = [];
                segments.Add(current);
            }
            current.Add(item);
            previous = item;
        }
        return segments;
    }

    private static bool AreContiguous(
        PredictionHistoryItemDto previous,
        PredictionHistoryItemDto current)
    {
        if (previous.SourceSerial != current.SourceSerial)
        {
            return false;
        }
        if (previous.RoundLocalDate != current.RoundLocalDate)
        {
            return false;
        }
        if (previous.RoundNumber.HasValue && current.RoundNumber.HasValue)
        {
            return current.RoundNumber.Value == previous.RoundNumber.Value + 1;
        }
        var elapsed = current.DetectedAtUtc - previous.DetectedAtUtc;
        return elapsed > TimeSpan.Zero && elapsed <= TimeSpan.FromMinutes(3);
    }

    private static int CurrentCategoryStreak(IReadOnlyList<PredictionHistoryItemDto> segment) =>
        segment.Count == 0 ? 0 : CategoryStreakEndingAt(segment, segment.Count - 1);

    private static int CategoryStreakEndingAt(
        IReadOnlyList<PredictionHistoryItemDto> segment,
        int endIndex)
    {
        var category = segment[endIndex].Category;
        var length = 1;
        for (var index = endIndex - 1; index >= 0 && segment[index].Category == category; index--)
        {
            length++;
        }
        return length;
    }

    private static string CategoryLabel(string category) =>
        category == "VEGETABLE" ? "Rau" : category == "MEAT" ? "Thịt" : category;

    private static Dictionary<string, double> Normalize(
        IReadOnlyDictionary<string, double> values,
        IReadOnlyList<string> codes,
        IReadOnlyDictionary<string, double>? fallback = null)
    {
        var total = codes.Sum(code => Math.Max(0, values.GetValueOrDefault(code)));
        if (total <= 0)
        {
            return fallback is null
                ? codes.ToDictionary(code => code, _ => 1d / codes.Count)
                : codes.ToDictionary(code => code, code => fallback[code]);
        }
        return codes.ToDictionary(
            code => code,
            code => Math.Max(0, values.GetValueOrDefault(code)) / total);
    }

    private sealed record MarketPredictionBuildResult(
        PredictionDto Prediction,
        MarketSideForecastDto SideForecast);

    private sealed record MarketSideSignal(
        double RedProbability,
        double GreenProbability,
        string? CurrentSide,
        int CurrentStreak,
        int MatchedTransitions,
        double SignalWeight);

}
