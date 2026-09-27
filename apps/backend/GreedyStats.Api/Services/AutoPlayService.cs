using System.Text.Json;
using GreedyStats.Api.Data;
using GreedyStats.Api.Models;

namespace GreedyStats.Api.Services;

public sealed class AutoPlayService : BackgroundService
{
    private const string StateKey = "autoplay_state_v2";
    private const int BetCountdownMaximum = 20;
    private const int BetCountdownMinimum = 13;
    private static readonly int[] AllowedChipValues = [2, 10, 50, 100, 1000];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { PropertyNameCaseInsensitive = true };

    private static readonly IReadOnlyList<AutoPlayStrategyDto> StrategyProfiles =
    [
        new("CAPITAL_GUARD", "Bảo toàn vốn", "Một cửa xác suất rõ nhất; ưu tiên bỏ cầu và bảo vệ 80% vốn.", 80, .5, 2, 2, 70, 6, 3, 15, 1, "THẤP"),
        new("VALUE_SINGLE", "Một cửa có giá trị", "Một cửa có kỳ vọng dương tốt nhất, không chạy theo cửa HOT.", 75, .75, 2.5, 3, 66, 3, 4, 45, 1, "VỪA"),
        new("BALANCED_MULTI", "Cân bằng nhiều cửa", "Phủ tối đa hai cửa cùng đạt bộ lọc xác suất và kỳ vọng.", 70, 1, 3, 3, 65, 1, 2, 25, 2, "VỪA"),
        new("TOP_TWO_COVER", "Phủ hai cửa", "Ưu tiên hai cửa xác suất cao, chỉ đặt cửa có kỳ vọng không âm.", 70, 1, 3, 3, 64, 0, 0, 25, 2, "VỪA"),
        new("DIVERSIFIED_THREE", "Phủ chọn lọc ba cửa", "Tối đa ba cửa đạt chuẩn; tổng tiền vẫn bị chặn theo vốn.", 65, 1.5, 4, 3, 64, 0, 1, 25, 3, "CAO")
    ];

    private readonly GreedyDatabase _database;
    private readonly PredictionService _predictions;
    private readonly PhoneControlService _phoneControl;
    private readonly ILogger<AutoPlayService> _logger;
    private readonly bool _liveExecutionAvailable;
    private readonly bool _liveTestModeEnabled;
    private readonly int _liveTestMaxRounds;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private StoredConfiguration _configuration = StoredConfiguration.Default;
    private readonly List<AutoPlayActionDto> _actions = [];
    private readonly List<StoredRun> _runs = [];
    private string? _currentRunId;
    private string _localDate = string.Empty;
    private decimal _todayStakeUnits;
    private decimal _todayNetUnits;
    private int _consecutiveLosses;
    private int _recoveryStep;
    private bool _initialized;
    private string _engineStatus = "DISABLED";
    private string _message = "Tự động chơi đang tắt.";
    private int? _activeRound;
    private int? _countdownSeconds;
    private string? _lastEvaluatedRound;
    private decimal? _detectedBalanceUnits;
    private DateTimeOffset? _balanceObservedAtUtc;
    private DateTimeOffset _lastSettlementCheckUtc = DateTimeOffset.MinValue;

    public AutoPlayService(GreedyDatabase database, PredictionService predictions,
        PhoneControlService phoneControl, IConfiguration configuration, ILogger<AutoPlayService> logger)
    {
        _database = database;
        _predictions = predictions;
        _phoneControl = phoneControl;
        _logger = logger;
        _liveExecutionAvailable = configuration.GetValue("AutoPlay:LiveExecutionEnabled", false);
        _liveTestModeEnabled = configuration.GetValue("AutoPlay:LiveTestModeEnabled", false);
        _liveTestMaxRounds = Math.Clamp(configuration.GetValue("AutoPlay:LiveTestMaxRounds", 3), 1, 10);
    }

    public async Task<AutoPlayStatusDto> GetStatusAsync(CancellationToken token = default)
    {
        await EnsureInitializedAsync(token);
        await _gate.WaitAsync(token);
        try { return BuildStatusLocked(); }
        finally { _gate.Release(); }
    }

    public async Task<AutoPlayStatusDto> SaveConfigurationAsync(AutoPlayConfigurationRequest request, CancellationToken token = default)
    {
        await EnsureInitializedAsync(token);
        var strategy = ResolveStrategy(request.Strategy);
        var mode = NormalizeMode(request.Mode);
        if (mode == "LIVE" && !_liveExecutionAvailable)
            throw new InvalidOperationException("LIVE đang khóa cho tới khi OCR số dư, tiền cược và tọa độ ADB được kiểm tra.");
        if (mode == "LIVE" && !request.LiveModeAcknowledged)
            throw new InvalidOperationException("Cần xác nhận rủi ro trước khi bật chế độ máy thật.");
        if (!AllowedChipValues.Contains(request.ChipValue))
            throw new InvalidOperationException("Mệnh giá xu phải là 2, 10, 50, 100 hoặc 1000.");
        if (mode == "LIVE" && _liveTestModeEnabled && request.Enabled &&
            (request.ChipValue != 2 || request.TapsPerItem != 1 || request.MaximumSelections != 1 || request.LossRecoveryEnabled))
            throw new InvalidOperationException("Chế độ thử máy thật chỉ cho phép 1 cửa × 1 lần chạm × xu 2, không gỡ lỗ.");
        if (request.MaxConsecutiveLosses is < 0 or > 20)
            throw new InvalidOperationException("Số lần thua liên tiếp phải từ 0 đến 20; chọn 0 để không dừng theo chuỗi thua.");
        if (request.MinimumVegetableProbabilityPercent is < 0 or > 100 ||
            request.MinimumMeatProbabilityPercent is < 0 or > 100)
            throw new InvalidOperationException("Ngưỡng xác suất Rau và Thịt phải từ 0% đến 100%.");
        if (request.MaximumSelections is < 1 or > 3)
            throw new InvalidOperationException("Số cửa tối đa mỗi round phải từ 1 đến 3.");
        if (request.LossRecoveryMultiplier is < 1 or > 3)
            throw new InvalidOperationException("Hệ số gỡ lỗ phải từ 1 đến 3.");
        if (request.MaximumRecoverySteps is < 0 or > 5)
            throw new InvalidOperationException("Số bước gỡ lỗ tối đa phải từ 0 đến 5.");
        var scanner = await _database.GetScannerStatusAsync(token);
        await _gate.WaitAsync(token);
        try
        {
            ResetDailyStateIfNeededLocked();
            var changedIdentity = _configuration.Enabled && (!request.Enabled || _configuration.Mode != mode || _configuration.Strategy != strategy.Key);
            if (changedIdentity) CloseCurrentRunLocked("STOPPED", scanner.Financials?.BalanceUnits);
            var wasEnabled = _configuration.Enabled;
            var strategySettings = NormalizeStrategySettings(_configuration.StrategySettings);
            strategySettings[strategy.Key] = new StoredStrategySettings(
                request.MaxConsecutiveLosses,
                request.MinimumVegetableProbabilityPercent,
                request.MinimumMeatProbabilityPercent,
                request.MaximumSelections,
                request.LossRecoveryEnabled,
                request.LossRecoveryMultiplier,
                request.MaximumRecoverySteps);
            _configuration = new StoredConfiguration(request.Enabled, mode, strategy.Key,
                Math.Clamp(request.BankrollUnits, 10m, 100_000_000m), request.ChipValue,
                Math.Clamp(request.TapsPerItem, 1, 5), strategySettings);
            if (request.Enabled && (!wasEnabled || changedIdentity || _currentRunId is null))
                StartRunLocked(mode == "LIVE" && scanner.Financials is not null ? scanner.Financials.BalanceUnits : _configuration.BankrollUnits);
            if (!request.Enabled) CloseCurrentRunLocked("STOPPED", scanner.Financials?.BalanceUnits);
            _engineStatus = request.Enabled ? "MONITORING" : "DISABLED";
            _message = request.Enabled
                ? mode == "LIVE" ? "Đang theo dõi máy thật; chỉ đặt khi xác minh được số dư." : "Đang mô phỏng; không gửi thao tác tới điện thoại."
                : "Tự động chơi đang tắt.";
            await PersistLockedAsync(token);
            return BuildStatusLocked();
        }
        finally { _gate.Release(); }
    }

    public async Task<AutoPlayStatusDto> EmergencyStopAsync(CancellationToken token = default)
    {
        await EnsureInitializedAsync(token);
        var scanner = await _database.GetScannerStatusAsync(token);
        await _gate.WaitAsync(token);
        try
        {
            _configuration = _configuration with { Enabled = false };
            CloseCurrentRunLocked("EMERGENCY_STOP", scanner.Financials?.BalanceUnits);
            _engineStatus = "EMERGENCY_STOP";
            _message = "Đã ngắt khẩn cấp. Không có lệnh mới nào được gửi.";
            await PersistLockedAsync(token);
            return BuildStatusLocked();
        }
        finally { _gate.Release(); }
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await EnsureInitializedAsync(stoppingToken);
        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(500));
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            try { await TickAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { return; }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Auto-play tick failed");
                await SetRuntimeMessageAsync("ERROR", $"Lỗi auto-play: {ex.Message}", stoppingToken);
            }
        }
    }

    private async Task TickAsync(CancellationToken token)
    {
        await SettlePendingActionsAsync(token);
        StoredConfiguration configuration;
        AutoPlayStrategyDto strategy;
        await _gate.WaitAsync(token);
        try
        {
            ResetDailyStateIfNeededLocked();
            configuration = _configuration;
            strategy = ResolveStrategy(configuration.Strategy);
        }
        finally { _gate.Release(); }
        if (!configuration.Enabled)
        {
            if (_engineStatus is not ("EMERGENCY_STOP" or "RISK_STOP" or "VERIFY_STOP"))
                await SetRuntimeMessageAsync("DISABLED", "Tự động chơi đang tắt.", token);
            return;
        }

        var scanner = await _database.GetScannerStatusAsync(token);
        await UpdateScannerTelemetryAsync(scanner, token);
        if (!scanner.IsOnline || scanner.ActiveRound is null || scanner.CountdownSeconds is null)
        {
            await SetRuntimeMessageAsync("WAITING_SCANNER", "Đang chờ scanner, round và đếm ngược hợp lệ.", token);
            return;
        }
        if (configuration.Mode == "LIVE" && scanner.Financials is null)
        {
            await SetRuntimeMessageAsync("WAITING_BALANCE", "Chưa đọc ổn định số dư máy thật; không đặt cược.", token);
            return;
        }
        if (configuration.Mode == "LIVE" && _liveTestModeEnabled &&
            scanner.Financials!.OwnBets.Values.Any(amount => amount != 0))
        {
            await SetRuntimeMessageAsync("WAITING_CLEAR_BETS", "Đợi các khoản cược hiện có kết thúc trước khi thử 2 xu.", token);
            return;
        }
        var round = scanner.ActiveRound.Value;
        var countdown = scanner.CountdownSeconds.Value;
        var date = _database.LocalToday.ToString("yyyy-MM-dd");
        if (await HasDecisionForRoundAsync(date, round, token)) return;
        if (countdown > BetCountdownMaximum)
        {
            await SetRuntimeMessageAsync("MONITORING", $"Round {round}: chờ cửa đặt {BetCountdownMaximum}–{BetCountdownMinimum} giây.", token);
            return;
        }
        if (countdown < BetCountdownMinimum)
        {
            await RecordSkipAsync(date, round, configuration, "Đã qua mốc 13 giây; bỏ round để tránh thao tác sát giờ.", "CAO", token);
            return;
        }

        // History-only predictor: HOT, crowd coins, side forecast and AI are intentionally excluded.
        var prediction = await _predictions.GetPredictionAsync(_database.LocalToday, token);
        var evaluation = Evaluate(configuration, strategy, prediction, scanner.Financials?.BalanceUnits);
        if (!evaluation.ShouldBet)
        {
            await RecordSkipAsync(date, round, configuration, evaluation.Reason, evaluation.RiskLevel, token, evaluation);
            return;
        }
        await ExecuteDecisionAsync(date, round, configuration, strategy, evaluation, scanner, token);
    }

    private DecisionEvaluation Evaluate(StoredConfiguration config, AutoPlayStrategyDto strategy,
        PredictionDto prediction, decimal? detectedBalance)
    {
        var settings = ResolveStrategySettings(config, strategy);
        var ordered = prediction.Items.OrderByDescending(item => item.ProbabilityPercent).ToList();
        if (ordered.Count < 2) return DecisionEvaluation.Skip("Không đủ hai cửa để đánh giá rủi ro.", "CAO");
        var gap = ordered[0].ProbabilityPercent - ordered[1].ProbabilityPercent;
        if (prediction.ModelConfidencePercent < strategy.MinimumConfidencePercent)
            return DecisionEvaluation.Skip($"Độ tin cậy {prediction.ModelConfidencePercent:0.#}% dưới ngưỡng {strategy.MinimumConfidencePercent:0.#}%.", "CAO", gap);
        if (gap < strategy.MinimumTopGapPercent)
            return DecisionEvaluation.Skip($"Khoảng cách hai cửa đầu {gap:0.#}% chưa đạt ngưỡng.", "CAO", gap);
        var candidates = ordered
            .Select(item => new { Item = item, Edge = item.ProbabilityPercent / 100d * item.PayoutMultiplier * 100d - 100d })
            .Where(x => x.Edge >= strategy.MinimumExpectedEdgePercent &&
                x.Item.PayoutMultiplier <= strategy.MaximumPayoutMultiplier &&
                x.Item.ProbabilityPercent >= (x.Item.Category == "VEGETABLE"
                    ? settings.MinimumVegetableProbabilityPercent
                    : settings.MinimumMeatProbabilityPercent))
            // Rank qualified choices by expected value first. Otherwise the
            // naturally more frequent vegetables would occupy every slot and
            // a strong, higher-paying meat signal would almost never be used.
            .OrderByDescending(x => x.Edge).ThenByDescending(x => x.Item.ProbabilityPercent)
            .Take(config.Mode == "LIVE" && _liveTestModeEnabled ? 1 : settings.MaximumSelections).ToList();
        if (candidates.Count == 0)
            return DecisionEvaluation.Skip(
                $"Không có cửa đạt bộ lọc: Rau ≥ {settings.MinimumVegetableProbabilityPercent:0.#}%, " +
                $"Thịt ≥ {settings.MinimumMeatProbabilityPercent:0.#}%, kỳ vọng và trần trả thưởng.", "CAO", gap);
        var balance = config.Mode == "LIVE" ? detectedBalance ?? 0 : config.BankrollUnits + _todayNetUnits;
        var reserve = config.BankrollUnits * (decimal)strategy.ReservePercent / 100m;
        var lossLimit = config.BankrollUnits * (decimal)strategy.MaxDailyLossPercent / 100m;
        if (_todayNetUnits <= -lossLimit) return DecisionEvaluation.Skip("Đã chạm giới hạn lỗ trong ngày.", "CAO", gap);
        if (settings.MaxConsecutiveLosses > 0 && _consecutiveLosses >= settings.MaxConsecutiveLosses)
            return DecisionEvaluation.Skip("Đã chạm giới hạn thua liên tiếp đã cấu hình; không gỡ lỗ.", "CAO", gap);
        var appliedRecoveryStep = settings.LossRecoveryEnabled && !(config.Mode == "LIVE" && _liveTestModeEnabled)
            ? Math.Min(_recoveryStep, settings.MaximumRecoverySteps)
            : 0;
        var stakeMultiplier = appliedRecoveryStep > 0
            ? Math.Pow(settings.LossRecoveryMultiplier, appliedRecoveryStep)
            : 1d;
        var effectiveTaps = config.Mode == "LIVE" && _liveTestModeEnabled ? 1 : Math.Clamp(
            (int)Math.Ceiling(config.TapsPerItem * stakeMultiplier),
            config.TapsPerItem,
            10);
        // Report the multiplier actually representable by whole screen taps.
        stakeMultiplier = effectiveTaps / (double)config.TapsPerItem;
        var perItem = (decimal)config.ChipValue * effectiveTaps;
        var maxExposure = config.Mode == "LIVE" && _liveTestModeEnabled
            ? 2m
            : Math.Floor(balance * (decimal)strategy.MaxStakePercent / 100m);
        while (candidates.Count > 0 && perItem * candidates.Count > maxExposure) candidates.RemoveAt(candidates.Count - 1);
        var total = perItem * candidates.Count;
        if (candidates.Count == 0 || total <= 0 || balance - total < reserve)
            return DecisionEvaluation.Skip("Kế hoạch xâm phạm giới hạn cược hoặc vốn dự phòng.", "CAO", gap);
        var bets = candidates.Select(x => new AutoPlayBetDto(x.Item.ItemCode, x.Item.ItemName, perItem,
            effectiveTaps, x.Item.PayoutMultiplier, x.Item.ProbabilityPercent, x.Edge)).ToList();
        return new DecisionEvaluation(true,
            appliedRecoveryStep > 0
                ? $"{strategy.Name}: bước gỡ {appliedRecoveryStep}/{settings.MaximumRecoverySteps}, " +
                  $"xu {config.ChipValue} × {effectiveTaps} lần/cửa (x{stakeMultiplier:0.##})."
                : $"{strategy.Name}: {bets.Count} cửa, xu {config.ChipValue} × {effectiveTaps} lần/cửa.",
            strategy.RiskLevel, gap, bets.Average(x => x.ExpectedEdgePercent), prediction.ModelConfidencePercent,
            appliedRecoveryStep, stakeMultiplier, bets);
    }

    private async Task ExecuteDecisionAsync(string date, int round, StoredConfiguration config,
        AutoPlayStrategyDto strategy, DecisionEvaluation evaluation, ScannerStatusDto scannerBefore, CancellationToken token)
    {
        var latest = await _database.GetScannerStatusAsync(token);
        if (latest.ActiveRound != round || latest.CountdownSeconds is null or < BetCountdownMinimum or > BetCountdownMaximum)
        {
            await RecordSkipAsync(date, round, config, "Round đã đổi hoặc dự đoán hoàn tất quá muộn; hủy trước khi chạm.", "CAO", token, evaluation);
            return;
        }
        if (config.Mode == "LIVE" && _liveTestModeEnabled &&
            (config.ChipValue != 2 || evaluation.Bets.Count != 1 ||
             evaluation.Bets[0].TapCount != 1 || evaluation.Bets[0].StakeUnits != 2 ||
             latest.Financials is null || latest.Financials.OwnBets.Values.Any(amount => amount != 0) ||
             latest.Financials.BalanceUnits < 2 ||
             DateTimeOffset.UtcNow - latest.Financials.ObservedAtUtc > TimeSpan.FromSeconds(3) ||
             scannerBefore.Financials is null ||
             latest.Financials.BalanceUnits != scannerBefore.Financials.BalanceUnits ||
             latest.CountdownObservedAtUtc is null ||
             DateTimeOffset.UtcNow - latest.CountdownObservedAtUtc > TimeSpan.FromSeconds(2)))
        {
            await RecordSkipAsync(date, round, config, "Thử máy thật: dữ liệu chưa đủ mới hoặc kế hoạch vượt 2 xu; bỏ cầu.", "CAO", token, evaluation);
            return;
        }
        await _gate.WaitAsync(token);
        try
        {
            if (!_configuration.Enabled || _configuration != config || _actions.Any(x => x.LocalDate == date && x.RoundNumber == round)) return;
        }
        finally { _gate.Release(); }

        var total = evaluation.Bets.Sum(x => x.StakeUnits);
        var status = config.Mode == "LIVE" ? "PLACED" : "SIMULATED";
        var reason = evaluation.Reason;
        var verification = new AutoPlayVerificationDto(config.Mode == "LIVE" ? "PENDING" : "SIMULATED",
            scannerBefore.Financials?.BalanceUnits, null, total, new Dictionary<string, decimal>(),
            config.Mode == "SIMULATION" ? "Mô phỏng không chạm điện thoại." : "Đang chờ scanner xác minh.");
        string? reservedActionId = null;
        PhoneControlService.PhoneBetPlacementResult? placement = null;
        if (config.Mode == "LIVE")
        {
            if (scannerBefore.Financials is null)
            {
                await RecordSkipAsync(date, round, config, "Không có số dư OCR mới; không gửi ADB.", "CAO", token, evaluation);
                return;
            }
            try
            {
                var phone = await _phoneControl.GetStatusAsync(token);
                if (!phone.Connected)
                {
                    await RecordSkipAsync(date, round, config, "Điện thoại ADB chưa kết nối; không gửi lệnh.", "CAO", token, evaluation);
                    return;
                }
                if (_liveTestModeEnabled)
                {
                    reservedActionId = Guid.NewGuid().ToString("N");
                    await _gate.WaitAsync(token);
                    try
                    {
                        if (!_configuration.Enabled || _configuration != config ||
                            _actions.Any(x => x.LocalDate == date && x.RoundNumber == round)) return;
                        _actions.Add(new AutoPlayActionDto(reservedActionId, _currentRunId, date, round,
                            config.Mode, strategy.Key, evaluation.Bets, total, evaluation.ConfidencePercent,
                            evaluation.TopGapPercent, evaluation.ExpectedEdgePercent, "PENDING", "Đã giữ lượt thử 2 xu trước khi gửi ADB.",
                            DateTimeOffset.UtcNow, null, null, null, evaluation.RiskLevel,
                            evaluation.RecoveryStep, evaluation.StakeMultiplier, verification));
                        await PersistLockedAsync(token);
                    }
                    finally { _gate.Release(); }
                }
                placement = await _phoneControl.PlaceBetsAsync(evaluation.Bets, config.ChipValue, token, round);
                reason += $" Tham gia: {placement.ParticipationDiamonds} kim cương, {placement.ParticipationStatus}.";
                verification = await VerifyLivePlacementAsync(scannerBefore.Financials, evaluation.Bets, total, token);
                if (verification.Status != "VERIFIED")
                {
                    status = "PLACED_UNVERIFIED";
                    reason += " Xác minh thất bại; đã dừng và không thử đặt lại.";
                }
            }
            catch (Exception ex)
            {
                status = "ERROR";
                reason = $"ADB không hoàn tất kế hoạch: {ex.Message}. Không tự thử lại.";
                verification = verification with { Status = "ERROR", Message = ex.Message };
            }
        }
        var action = new AutoPlayActionDto(reservedActionId ?? Guid.NewGuid().ToString("N"), _currentRunId, date, round,
            config.Mode, strategy.Key, evaluation.Bets, total, evaluation.ConfidencePercent,
            evaluation.TopGapPercent, evaluation.ExpectedEdgePercent, status, reason, DateTimeOffset.UtcNow,
            null, null, null, evaluation.RiskLevel, evaluation.RecoveryStep, evaluation.StakeMultiplier, verification)
        {
            ParticipationDiamonds = placement?.ParticipationDiamonds ?? 0,
            ParticipationStatus = placement?.ParticipationStatus
        };
        await _gate.WaitAsync(token);
        try
        {
            if (reservedActionId is null)
            {
                if (_actions.Any(x => x.LocalDate == date && x.RoundNumber == round)) return;
                _actions.Add(action);
            }
            else
            {
                var reservedIndex = _actions.FindIndex(x => x.Id == reservedActionId);
                if (reservedIndex < 0) return;
                _actions[reservedIndex] = action;
            }
            if (status == "SIMULATED" || (status == "PLACED" && verification.Status == "VERIFIED"))
                _todayStakeUnits += total;
            _engineStatus = status == "PLACED_UNVERIFIED" ? "VERIFY_STOP" : status;
            _message = reason;
            _lastEvaluatedRound = $"{date}#{round}";
            var testRounds = _actions.Count(x => x.RunId == _currentRunId && x.Mode == "LIVE" &&
                x.Status != "SKIPPED" && x.TotalStakeUnits > 0);
            if (status is "PLACED_UNVERIFIED" or "ERROR" ||
                (config.Mode == "LIVE" && _liveTestModeEnabled && testRounds >= _liveTestMaxRounds))
            {
                _configuration = _configuration with { Enabled = false };
                var stopStatus = status == "ERROR" ? "ERROR" : status == "PLACED_UNVERIFIED" ? "VERIFY_STOP" : "TEST_COMPLETE";
                _engineStatus = stopStatus;
                CloseCurrentRunLocked(stopStatus, verification.BalanceAfterUnits);
            }
            TrimHistoryLocked();
            await PersistLockedAsync(token);
        }
        finally { _gate.Release(); }
    }

    private async Task<AutoPlayVerificationDto> VerifyLivePlacementAsync(ScannerFinancialsDto before,
        IReadOnlyList<AutoPlayBetDto> bets, decimal total, CancellationToken token)
    {
        // One OCR job reads the balance plus eight per-item regions. Allow a
        // full second fresh frame even when an older OCR job was in flight at
        // the exact moment the ADB taps completed.
        var deadline = DateTimeOffset.UtcNow.AddSeconds(8);
        ScannerFinancialsDto? after = null;
        while (DateTimeOffset.UtcNow < deadline)
        {
            await Task.Delay(350, token);
            var scanner = await _database.GetScannerStatusAsync(token);
            if (scanner.Financials is not { } current || current.ObservedAtUtc <= before.ObservedAtUtc) continue;
            after = current;
            var balanceMatches = current.BalanceUnits == before.BalanceUnits - total;
            var betsMatch = bets.All(bet => current.OwnBets.TryGetValue(bet.ItemCode, out var amount) &&
                amount - before.OwnBets.GetValueOrDefault(bet.ItemCode) == bet.StakeUnits);
            if (balanceMatches && betsMatch)
                return new("VERIFIED", before.BalanceUnits, current.BalanceUnits, total, current.OwnBets,
                    "Số dư và tiền dưới từng vật phẩm khớp kế hoạch.");
        }
        return new(after is null ? "TIMEOUT" : "MISMATCH", before.BalanceUnits, after?.BalanceUnits,
            total, after?.OwnBets ?? new Dictionary<string, decimal>(),
            after is null ? "Không có ảnh OCR mới trong thời hạn." : "Số dư hoặc tiền theo vật phẩm không khớp.");
    }

    private async Task RecordSkipAsync(string date, int round, StoredConfiguration config, string reason,
        string risk, CancellationToken token, DecisionEvaluation? evaluation = null)
    {
        await _gate.WaitAsync(token);
        try
        {
            if (_actions.Any(x => x.LocalDate == date && x.RoundNumber == round)) return;
            _actions.Add(new AutoPlayActionDto(Guid.NewGuid().ToString("N"), _currentRunId, date, round,
                config.Mode, config.Strategy, evaluation?.Bets ?? [], 0, evaluation?.ConfidencePercent ?? 0,
                evaluation?.TopGapPercent ?? 0, evaluation?.ExpectedEdgePercent ?? 0, "SKIPPED", reason,
                DateTimeOffset.UtcNow, null, null, 0, risk,
                evaluation is { RecoveryStep: > 0 } ? evaluation.RecoveryStep : _recoveryStep,
                evaluation?.StakeMultiplier ?? 1,
                new("NOT_REQUIRED", _detectedBalanceUnits, _detectedBalanceUnits, 0,
                    new Dictionary<string, decimal>(), "Round không đặt cược.")));
            _engineStatus = "SKIPPED";
            _lastEvaluatedRound = $"{date}#{round}";
            _message = reason;
            TrimHistoryLocked();
            await PersistLockedAsync(token);
        }
        finally { _gate.Release(); }
    }

    private async Task SettlePendingActionsAsync(CancellationToken token)
    {
        var now = DateTimeOffset.UtcNow;
        if (now - _lastSettlementCheckUtc < TimeSpan.FromSeconds(1)) return;
        _lastSettlementCheckUtc = now;
        List<AutoPlayActionDto> pending;
        await _gate.WaitAsync(token);
        try { pending = _actions.Where(x => x.ResultItemCode is null && x.Status != "ERROR").ToList(); }
        finally { _gate.Release(); }
        var recentResults = await _database.GetRecentRoundResultsAsync(800, token);
        var found = pending
            .Select(action => new { action.Id, Result = recentResults.GetValueOrDefault($"{action.LocalDate}#{action.RoundNumber}") })
            .Where(value => value.Result is not null)
            .ToDictionary(value => value.Id, value => value.Result!);
        if (found.Count == 0) return;
        await _gate.WaitAsync(token);
        try
        {
            foreach (var action in pending)
            {
                if (!found.TryGetValue(action.Id, out var result)) continue;
                var index = _actions.FindIndex(x => x.Id == action.Id);
                if (index < 0 || _actions[index].ResultItemCode is not null) continue;
                decimal net = 0;
                var nextStatus = action.Status;
                if (action.Mode == "LIVE" && action.TotalStakeUnits > 0 &&
                    action.Verification.Status != "VERIFIED")
                {
                    _actions[index] = action with
                    {
                        Status = "UNVERIFIED",
                        SettledAtUtc = DateTimeOffset.UtcNow,
                        ResultItemCode = result,
                        NetUnits = null
                    };
                    continue;
                }
                if (action.TotalStakeUnits > 0)
                {
                    var winner = action.Bets.FirstOrDefault(x => string.Equals(x.ItemCode, result, StringComparison.OrdinalIgnoreCase));
                    net = winner is null ? -action.TotalStakeUnits : winner.StakeUnits * (decimal)winner.PayoutMultiplier - action.TotalStakeUnits;
                    nextStatus = net > 0 ? "WON" : net < 0 ? "LOST" : "BREAK_EVEN";
                }
                _actions[index] = action with { Status = nextStatus, SettledAtUtc = DateTimeOffset.UtcNow, ResultItemCode = result, NetUnits = net };
                if (action.LocalDate == _localDate && action.TotalStakeUnits > 0)
                {
                    _todayNetUnits += net;
                    _consecutiveLosses = net >= 0 ? 0 : _consecutiveLosses + 1;
                    _recoveryStep = net >= 0 ? 0 : _recoveryStep + 1;
                    var active = ResolveStrategy(_configuration.Strategy);
                    var activeSettings = ResolveStrategySettings(_configuration, active);
                    var limit = _configuration.BankrollUnits * (decimal)active.MaxDailyLossPercent / 100m;
                    if ((activeSettings.MaxConsecutiveLosses > 0 &&
                         _consecutiveLosses >= activeSettings.MaxConsecutiveLosses) ||
                        _todayNetUnits <= -limit)
                    {
                        _configuration = _configuration with { Enabled = false };
                        _engineStatus = "RISK_STOP";
                        _message = "Đã tự dừng do chạm giới hạn rủi ro; không gỡ lỗ.";
                        CloseCurrentRunLocked("RISK_STOP", _detectedBalanceUnits);
                    }
                }
            }
            await PersistLockedAsync(token);
        }
        finally { _gate.Release(); }
    }

    private async Task<bool> HasDecisionForRoundAsync(string date, int round, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try { return _actions.Any(x => x.LocalDate == date && x.RoundNumber == round); }
        finally { _gate.Release(); }
    }
    private async Task UpdateScannerTelemetryAsync(ScannerStatusDto scanner, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try
        {
            _activeRound = scanner.ActiveRound;
            _countdownSeconds = scanner.CountdownSeconds;
            _detectedBalanceUnits = scanner.Financials?.BalanceUnits;
            _balanceObservedAtUtc = scanner.Financials?.ObservedAtUtc;
        }
        finally { _gate.Release(); }
    }
    private async Task SetRuntimeMessageAsync(string status, string message, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try { _engineStatus = status; _message = message; }
        finally { _gate.Release(); }
    }

    private async Task EnsureInitializedAsync(CancellationToken token)
    {
        if (_initialized) return;
        await _gate.WaitAsync(token);
        try
        {
            if (_initialized) return;
            var raw = await _database.GetStateValueAsync(StateKey, token);
            if (!string.IsNullOrWhiteSpace(raw))
            {
                try
                {
                    var state = JsonSerializer.Deserialize<PersistentState>(raw, JsonOptions);
                    if (state is not null)
                    {
                        _configuration = NormalizeConfiguration(state.Configuration);
                        _localDate = state.LocalDate ?? string.Empty;
                        _todayStakeUnits = state.TodayStakeUnits;
                        _todayNetUnits = state.TodayNetUnits;
                        _consecutiveLosses = state.ConsecutiveLosses;
                        _recoveryStep = state.RecoveryStep;
                        _currentRunId = state.CurrentRunId;
                        _actions.AddRange(state.Actions ?? []);
                        _runs.AddRange(state.Runs ?? []);
                    }
                }
                catch (JsonException ex) { _logger.LogWarning(ex, "Cannot read auto-play v2 state; safe defaults used"); }
            }
            if (_configuration.Mode == "LIVE" && !_liveExecutionAvailable)
            {
                _configuration = _configuration with { Enabled = false, Mode = "SIMULATION" };
                CloseCurrentRunLocked("SAFETY_LOCK", null);
            }
            if (_configuration.Mode == "LIVE" && _liveTestModeEnabled && _currentRunId is not null &&
                (_actions.Any(x => x.RunId == _currentRunId && x.Status == "PENDING") ||
                 _actions.Count(x => x.RunId == _currentRunId && x.Mode == "LIVE" &&
                     x.Status != "SKIPPED" && x.TotalStakeUnits > 0) >= _liveTestMaxRounds))
            {
                _configuration = _configuration with { Enabled = false };
                CloseCurrentRunLocked("TEST_INTERRUPTED", null);
            }
            ResetDailyStateIfNeededLocked();
            if (_configuration.Enabled && _currentRunId is null)
            {
                var scanner = await _database.GetScannerStatusAsync(token);
                StartRunLocked(_configuration.Mode == "LIVE" && scanner.Financials is not null
                    ? scanner.Financials.BalanceUnits : _configuration.BankrollUnits);
            }
            _initialized = true;
            await PersistLockedAsync(token);
        }
        finally { _gate.Release(); }
    }

    private AutoPlayStatusDto BuildStatusLocked()
    {
        var strategy = ResolveStrategy(_configuration.Strategy);
        var settings = NormalizeStrategySettings(_configuration.StrategySettings);
        var recent = _actions.OrderByDescending(x => x.CreatedAtUtc).Take(50).ToList();
        return new(new(_configuration.Enabled, _configuration.Mode, strategy.Key, _configuration.BankrollUnits,
                _configuration.ChipValue, _configuration.TapsPerItem, _liveExecutionAvailable,
                _detectedBalanceUnits, _balanceObservedAtUtc,
                StrategyProfiles.Select(profile => ToStrategySettingsDto(profile.Key, settings[profile.Key])).ToList()),
            StrategyProfiles, _engineStatus, _message, _activeRound, _countdownSeconds, _lastEvaluatedRound,
            _todayStakeUnits, _todayNetUnits, _consecutiveLosses, _recoveryStep,
            _configuration.BankrollUnits * (decimal)strategy.ReservePercent / 100m,
            recent.FirstOrDefault(), recent,
            _currentRunId is null ? null : BuildRunDtoLocked(_runs.FirstOrDefault(x => x.Id == _currentRunId)),
            _runs.OrderByDescending(x => x.StartedAtUtc).Take(20).Select(BuildRunDtoLocked).Where(x => x is not null).Cast<AutoPlayRunDto>().ToList());
    }
    private AutoPlayRunDto? BuildRunDtoLocked(StoredRun? run)
    {
        if (run is null) return null;
        var actions = _actions.Where(x => x.RunId == run.Id).ToList();
        var net = actions.Sum(x => x.NetUnits ?? 0);
        var confirmed = actions.Where(x => x.Mode != "LIVE" || x.Verification.Status == "VERIFIED").ToList();
        return new(run.Id, run.Mode, run.Strategy, run.StartedAtUtc, run.EndedAtUtc, run.StartingBalanceUnits,
            run.EndingBalanceUnits ?? run.StartingBalanceUnits + net, run.EndingBalanceUnits, net,
            confirmed.Sum(x => x.TotalStakeUnits), confirmed.Count(x => x.TotalStakeUnits > 0),
            actions.Count(x => x.Status == "SKIPPED"), actions.Count(x => x.Status == "WON"),
            actions.Count(x => x.Status == "LOST"), actions.Count(x => x.Status == "SKIPPED" && x.RiskLevel == "CAO"), run.Status)
        {
            ParticipationDiamonds = actions.Sum(x => x.ParticipationDiamonds)
        };
    }
    private void StartRunLocked(decimal balance)
    {
        _recoveryStep = 0;
        var run = new StoredRun(Guid.NewGuid().ToString("N"), _configuration.Mode, _configuration.Strategy,
            DateTimeOffset.UtcNow, null, balance, null, "RUNNING");
        _runs.Add(run);
        _currentRunId = run.Id;
    }
    private void CloseCurrentRunLocked(string status, decimal? endingBalance)
    {
        if (_currentRunId is null) return;
        var index = _runs.FindIndex(x => x.Id == _currentRunId);
        if (index >= 0)
        {
            var run = _runs[index];
            _runs[index] = run with
            {
                EndedAtUtc = DateTimeOffset.UtcNow,
                EndingBalanceUnits = run.Mode == "LIVE" ? endingBalance : null,
                Status = status
            };
        }
        _currentRunId = null;
    }
    private async Task PersistLockedAsync(CancellationToken token)
    {
        var state = new PersistentState(_configuration, _localDate, _todayStakeUnits, _todayNetUnits,
            _consecutiveLosses, _recoveryStep, _currentRunId, _actions.ToList(), _runs.ToList());
        await _database.SetStateValueAsync(StateKey, JsonSerializer.Serialize(state, JsonOptions), token);
    }
    private void ResetDailyStateIfNeededLocked()
    {
        var today = _database.LocalToday.ToString("yyyy-MM-dd");
        if (_localDate == today) return;
        _localDate = today;
        _todayStakeUnits = 0;
        _todayNetUnits = 0;
        _consecutiveLosses = 0;
        _recoveryStep = 0;
        _lastEvaluatedRound = null;
    }
    private void TrimHistoryLocked()
    {
        if (_actions.Count > 500) _actions.RemoveRange(0, _actions.Count - 500);
        if (_runs.Count > 50) _runs.RemoveRange(0, _runs.Count - 50);
    }
    private static StoredConfiguration NormalizeConfiguration(StoredConfiguration? value)
    {
        if (value is null) return StoredConfiguration.Default;
        return value with { Mode = NormalizeMode(value.Mode), Strategy = ResolveStrategy(value.Strategy).Key,
            BankrollUnits = Math.Clamp(value.BankrollUnits, 10m, 100_000_000m),
            ChipValue = AllowedChipValues.Contains(value.ChipValue) ? value.ChipValue : 2,
            TapsPerItem = Math.Clamp(value.TapsPerItem, 1, 5),
            StrategySettings = NormalizeStrategySettings(value.StrategySettings) };
    }
    private static StoredStrategySettings ResolveStrategySettings(StoredConfiguration config, AutoPlayStrategyDto strategy)
    {
        var settings = NormalizeStrategySettings(config.StrategySettings);
        return settings[strategy.Key];
    }
    private static Dictionary<string, StoredStrategySettings> NormalizeStrategySettings(
        Dictionary<string, StoredStrategySettings>? values)
    {
        var normalized = new Dictionary<string, StoredStrategySettings>(StringComparer.OrdinalIgnoreCase);
        foreach (var profile in StrategyProfiles)
        {
            var fallback = DefaultStrategySettings(profile);
            var value = values is not null && values.TryGetValue(profile.Key, out var saved) ? saved : fallback;
            normalized[profile.Key] = new StoredStrategySettings(
                Math.Clamp(value.MaxConsecutiveLosses, 0, 20),
                Math.Clamp(value.MinimumVegetableProbabilityPercent, 0, 100),
                Math.Clamp(value.MinimumMeatProbabilityPercent, 0, 100),
                Math.Clamp(value.MaximumSelections, 1, 3),
                value.LossRecoveryMultiplier <= 0 ? fallback.LossRecoveryEnabled : value.LossRecoveryEnabled,
                value.LossRecoveryMultiplier <= 0
                    ? fallback.LossRecoveryMultiplier
                    : Math.Clamp(value.LossRecoveryMultiplier, 1, 3),
                value.LossRecoveryMultiplier <= 0
                    ? fallback.MaximumRecoverySteps
                    : Math.Clamp(value.MaximumRecoverySteps, 0, 5));
        }
        return normalized;
    }
    private static StoredStrategySettings DefaultStrategySettings(AutoPlayStrategyDto profile) => profile.Key switch
    {
        "CAPITAL_GUARD" => new(profile.MaxConsecutiveLosses, 15, 7, profile.MaximumSelections, true, 2, 2),
        "VALUE_SINGLE" => new(profile.MaxConsecutiveLosses, 14, 5, profile.MaximumSelections, true, 2, 3),
        "BALANCED_MULTI" => new(profile.MaxConsecutiveLosses, 13, 4, profile.MaximumSelections, true, 2, 2),
        "TOP_TWO_COVER" => new(profile.MaxConsecutiveLosses, 12, 3, profile.MaximumSelections, true, 2, 2),
        _ => new(profile.MaxConsecutiveLosses, 10, 2, profile.MaximumSelections, true, 1.5, 3)
    };
    private static AutoPlayStrategySettingsDto ToStrategySettingsDto(string key, StoredStrategySettings value) =>
        new(key, value.MaxConsecutiveLosses, value.MinimumVegetableProbabilityPercent,
            value.MinimumMeatProbabilityPercent, value.MaximumSelections, value.LossRecoveryEnabled,
            value.LossRecoveryMultiplier, value.MaximumRecoverySteps);
    private static AutoPlayStrategyDto ResolveStrategy(string? key) => StrategyProfiles.FirstOrDefault(x => string.Equals(x.Key, key, StringComparison.OrdinalIgnoreCase)) ?? StrategyProfiles[0];
    private static string NormalizeMode(string? mode) => string.Equals(mode, "LIVE", StringComparison.OrdinalIgnoreCase) ? "LIVE" : "SIMULATION";

    private sealed record StoredConfiguration(bool Enabled, string Mode, string Strategy, decimal BankrollUnits,
        int ChipValue, int TapsPerItem, Dictionary<string, StoredStrategySettings>? StrategySettings)
    {
        public static StoredConfiguration Default { get; } = new(false, "SIMULATION", "CAPITAL_GUARD", 1000m, 2, 1, null);
    }
    private sealed record StoredStrategySettings(int MaxConsecutiveLosses,
        double MinimumVegetableProbabilityPercent, double MinimumMeatProbabilityPercent, int MaximumSelections,
        bool LossRecoveryEnabled, double LossRecoveryMultiplier, int MaximumRecoverySteps);
    private sealed record StoredRun(string Id, string Mode, string Strategy, DateTimeOffset StartedAtUtc,
        DateTimeOffset? EndedAtUtc, decimal StartingBalanceUnits, decimal? EndingBalanceUnits, string Status);
    private sealed record PersistentState(StoredConfiguration? Configuration, string? LocalDate, decimal TodayStakeUnits,
        decimal TodayNetUnits, int ConsecutiveLosses, int RecoveryStep, string? CurrentRunId,
        List<AutoPlayActionDto>? Actions, List<StoredRun>? Runs);
    private sealed record DecisionEvaluation(bool ShouldBet, string Reason, string RiskLevel, double TopGapPercent,
        double ExpectedEdgePercent, double ConfidencePercent, int RecoveryStep, double StakeMultiplier,
        IReadOnlyList<AutoPlayBetDto> Bets)
    {
        public static DecisionEvaluation Skip(string reason, string risk, double gap = 0) =>
            new(false, reason, risk, gap, 0, 0, 0, 1, []);
    }
}
