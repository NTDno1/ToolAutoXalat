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

}
