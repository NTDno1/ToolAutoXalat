using System.Text.Json;

namespace GreedyStats.Api.Models;

public sealed record ResultDto(
    long Id,
    int? RoundNumber,
    string? RoundLocalDate,
    string SourceSerial,
    string ItemCode,
    string ItemName,
    string Category,
    DateTimeOffset DetectedAtUtc,
    double Confidence,
    IReadOnlyList<string> Sequence,
    string DetectionReason,
    string? CapturePath,
    int StreakLength);

public sealed record PageDto<T>(
    IReadOnlyList<T> Items,
    int Page,
    int PageSize,
    long TotalItems,
    int TotalPages);

public sealed record StreakRunItemDto(
    long ResultId,
    int? RoundNumber,
    string ItemCode,
    string ItemName,
    DateTimeOffset DetectedAtUtc);

public sealed record StreakRunDto(
    string Category,
    int Length,
    long StartResultId,
    long EndResultId,
    int? StartRound,
    int? EndRound,
    DateTimeOffset StartedAtUtc,
    DateTimeOffset EndedAtUtc,
    IReadOnlyList<StreakRunItemDto> Items);

public sealed record StreakBucketDto(
    int Length,
    int Count,
    IReadOnlyList<StreakRunDto> Runs);

public sealed record StreakRunSummaryDto(
    string Category,
    int Length,
    long StartResultId,
    long EndResultId,
    int? StartRound,
    int? EndRound,
    DateTimeOffset StartedAtUtc,
    DateTimeOffset EndedAtUtc);

public sealed record StreakBucketSummaryDto(
    int Length,
    int Count);

public sealed record TodayStatsDto(
    string LocalDate,
    long TotalResults,
    long RoundCount,
    long MissedRoundCount,
    long VegetableCount,
    long MeatCount,
    long SpecialCount,
    int CurrentVegetableStreak,
    int CurrentMeatStreak,
    int LongestVegetableStreak,
    int LongestMeatStreak,
    IReadOnlyDictionary<string, long> ItemCounts,
    IReadOnlyList<StreakRunDto> VegetableRuns,
    IReadOnlyList<StreakRunDto> MeatRuns,
    IReadOnlyList<StreakBucketDto> VegetableStreakBuckets,
    IReadOnlyList<StreakBucketDto> MeatStreakBuckets,
    ResultDto? LatestResult);

public sealed record CompactTodayStatsDto(
    string LocalDate,
    long TotalResults,
    long RoundCount,
    long MissedRoundCount,
    long VegetableCount,
    long MeatCount,
    long SpecialCount,
    int CurrentVegetableStreak,
    int CurrentMeatStreak,
    int LongestVegetableStreak,
    int LongestMeatStreak,
    IReadOnlyDictionary<string, long> ItemCounts,
    IReadOnlyList<StreakRunSummaryDto> VegetableRuns,
    IReadOnlyList<StreakRunSummaryDto> MeatRuns,
    IReadOnlyList<StreakBucketSummaryDto> VegetableStreakBuckets,
    IReadOnlyList<StreakBucketSummaryDto> MeatStreakBuckets,
    ResultDto? LatestResult);

public sealed record DailySummaryDto(
    string LocalDate,
    long TotalResults,
    long RoundCount,
    long MissedRoundCount,
    long VegetableCount,
    long MeatCount,
    long SpecialCount,
    int LongestVegetableStreak,
    int LongestMeatStreak);

public sealed record ScannerEventDto(
    long Id,
    string Severity,
    string EventCode,
    string Message,
    JsonElement Details,
    DateTimeOffset OccurredAtUtc,
    bool Acknowledged);

public sealed record ScannerEventRequest(
    string? SourceKey,
    string Severity,
    string EventCode,
    string Message,
    JsonElement? Details,
    DateTimeOffset? OccurredAtUtc);

public sealed record BettingSignalItemDto(
    string ItemCode,
    int CoinCount,
    int ActivityPercent);

public sealed record BettingSignalsDto(
    int? Round,
    DateTimeOffset ObservedAtUtc,
    int CountdownSeconds,
    string? HotItemCode,
    IReadOnlyList<BettingSignalItemDto> Items);

public sealed record BettingSignalSnapshotDto(
    long Id,
    string SourceSerial,
    string RoundLocalDate,
    int RoundNumber,
    DateTimeOffset ObservedAtUtc,
    string? HotItemCode,
    IReadOnlyList<BettingSignalItemDto> Items,
    string? ResultItemCode);

public sealed record ScannerFinancialsDto(
    DateTimeOffset ObservedAtUtc,
    decimal BalanceUnits,
    IReadOnlyDictionary<string, decimal> OwnBets);

public sealed record ScannerStatusDto(
    string Status,
    bool IsOnline,
    DateTimeOffset? LastHeartbeatUtc,
    long? LastResultId,
    string? LastResultRevision,
    IReadOnlyList<string> LastSequence,
    int OfflineAfterSeconds,
    string? SourceSerial,
    int? CurrentRound,
    int? ActiveRound,
    int? CountdownSeconds,
    DateTimeOffset? CountdownObservedAtUtc,
    BettingSignalsDto? BettingSignals,
    ScannerFinancialsDto? Financials,
    DateTimeOffset ServerUtc);

public sealed record PhoneControlStatusDto(
    bool Enabled,
    bool Connected,
    string? Serial,
    string? Model,
    int? Width,
    int? Height,
    string? Message);

public sealed record PhoneTapRequest(
    double X,
    double Y);

public sealed record PhoneSwipeRequest(
    double StartX,
    double StartY,
    double EndX,
    double EndY,
    int DurationMs);

public sealed record PhoneKeyRequest(
    int KeyCode);

public sealed record AutoPlayStrategyDto(
    string Key,
    string Name,
    string Description,
    double ReservePercent,
    double MaxStakePercent,
    double MaxDailyLossPercent,
    int MaxConsecutiveLosses,
    double MinimumConfidencePercent,
    double MinimumTopGapPercent,
    double MinimumExpectedEdgePercent,
    double MaximumPayoutMultiplier,
    int MaximumSelections,
    string RiskLevel);

public sealed record AutoPlayConfigurationDto(
    bool Enabled,
    string Mode,
    string Strategy,
    decimal BankrollUnits,
    int ChipValue,
    int TapsPerItem,
    bool LiveExecutionAvailable,
    decimal? DetectedBalanceUnits,
    DateTimeOffset? BalanceObservedAtUtc,
    IReadOnlyList<AutoPlayStrategySettingsDto> StrategySettings);

public sealed record AutoPlayStrategySettingsDto(
    string Strategy,
    int MaxConsecutiveLosses,
    double MinimumVegetableProbabilityPercent,
    double MinimumMeatProbabilityPercent,
    int MaximumSelections,
    bool LossRecoveryEnabled,
    double LossRecoveryMultiplier,
    int MaximumRecoverySteps);

public sealed record AutoPlayConfigurationRequest(
    bool Enabled,
    string Mode,
    string Strategy,
    decimal BankrollUnits,
    int ChipValue,
    int TapsPerItem,
    int MaxConsecutiveLosses,
    double MinimumVegetableProbabilityPercent,
    double MinimumMeatProbabilityPercent,
    int MaximumSelections,
    bool LossRecoveryEnabled,
    double LossRecoveryMultiplier,
    int MaximumRecoverySteps,
    bool LiveModeAcknowledged);

public sealed record AutoPlayBetDto(
    string ItemCode,
    string ItemName,
    decimal StakeUnits,
    int TapCount,
    double PayoutMultiplier,
    double ProbabilityPercent,
    double ExpectedEdgePercent);

public sealed record AutoPlayVerificationDto(
    string Status,
    decimal? BalanceBeforeUnits,
    decimal? BalanceAfterUnits,
    decimal ExpectedDebitUnits,
    IReadOnlyDictionary<string, decimal> ScannedBets,
    string Message);

public sealed record AutoPlayActionDto(
    string Id,
    string? RunId,
    string LocalDate,
    int RoundNumber,
    string Mode,
    string Strategy,
    IReadOnlyList<AutoPlayBetDto> Bets,
    decimal TotalStakeUnits,
    double ConfidencePercent,
    double TopGapPercent,
    double ExpectedEdgePercent,
    string Status,
    string Reason,
    DateTimeOffset CreatedAtUtc,
    DateTimeOffset? SettledAtUtc,
    string? ResultItemCode,
    decimal? NetUnits,
    string RiskLevel,
    int RecoveryStep,
    double StakeMultiplier,
    AutoPlayVerificationDto Verification)
{
    public int ParticipationDiamonds { get; init; }
    public string? ParticipationStatus { get; init; }
}

public sealed record AutoPlayRunDto(
    string Id,
    string Mode,
    string Strategy,
    DateTimeOffset StartedAtUtc,
    DateTimeOffset? EndedAtUtc,
    decimal StartingBalanceUnits,
    decimal CurrentBalanceUnits,
    decimal? EndingBalanceUnits,
    decimal NetUnits,
    decimal TotalStakeUnits,
    int BetRounds,
    int SkippedRounds,
    int WonRounds,
    int LostRounds,
    int HighRiskSkippedRounds,
    string Status)
{
    public int ParticipationDiamonds { get; init; }
}

public sealed record AutoPlayStatusDto(
    AutoPlayConfigurationDto Configuration,
    IReadOnlyList<AutoPlayStrategyDto> Strategies,
    string EngineStatus,
    string Message,
    int? ActiveRound,
    int? CountdownSeconds,
    string? LastEvaluatedRound,
    decimal TodayStakeUnits,
    decimal TodayNetUnits,
    int ConsecutiveLosses,
    int CurrentRecoveryStep,
    decimal ProtectedReserveUnits,
    AutoPlayActionDto? LastAction,
    IReadOnlyList<AutoPlayActionDto> RecentActions,
    AutoPlayRunDto? CurrentRun,
    IReadOnlyList<AutoPlayRunDto> RecentRuns);

public sealed record AlertDeliveryDto(
    long Id,
    string AlertKey,
    string RuleCode,
    string Category,
    int StreakLength,
    long ResultId,
    string Status,
    int? ResponseStatus,
    DateTimeOffset CreatedAtUtc,
    DateTimeOffset? AttemptedAtUtc);

public sealed record AlertCandidate(
    string AlertKey,
    string RuleCode,
    string Category,
    int StreakLength,
    long ResultId,
    JsonElement Payload);

public sealed record AlertWebhookConfigDto(
    bool Enabled,
    bool Configured,
    string WebhookUrl,
    DateTimeOffset? UpdatedAtUtc);

public sealed record AlertWebhookConfigRequest(
    bool Enabled,
    string? WebhookUrl);

public sealed record SubscriberDto(
    long Id,
    string PhoneNumber,
    string? DisplayName,
    bool IsActive,
    DateTimeOffset CreatedAtUtc,
    DateTimeOffset UpdatedAtUtc);

public sealed record SubscriberRequest(
    string PhoneNumber,
    string? DisplayName);

public sealed record PaymentConfigDto(
    bool IsConfigured,
    string QrImageUrl,
    decimal Amount,
    string Currency,
    string BankName,
    string AccountName,
    string AccountNumber,
    string TransferPrefix,
    string Instructions);

public sealed record PredictionItemDto(
    string ItemCode,
    string ItemName,
    string Category,
    double PayoutMultiplier,
    long HistoricalCount,
    long TodayCount,
    double ProbabilityPercent,
    double SignalScore,
    int PatternMatches,
    string Reason);

public sealed record PredictionDto(
    string Provider,
    string LocalDate,
    DateTimeOffset GeneratedAtUtc,
    IReadOnlyList<PredictionItemDto> Items,
    string MethodNote,
    double ModelConfidencePercent,
    double VegetableProbabilityPercent,
    double MeatProbabilityPercent,
    string LeadingItemCode);

public sealed record HousePerformanceDto(
    int AnalyzedRounds,
    double EstimatedStakeUnits,
    double EstimatedPayoutUnits,
    double EstimatedNetUnits,
    double EstimatedMarginPercent,
    int HouseWinningRounds,
    int HouseLosingRounds,
    int HotObservedRounds,
    double HotHitRatePercent,
    double RiskAvoidanceScorePercent,
    string? HighestCurrentLiabilityItemCode,
    double HighestCurrentLiabilityUnits,
    double CurrentEstimatedStakeUnits,
    IReadOnlyList<HouseOutcomeScenarioDto> CurrentOutcomeScenarios);

public sealed record HouseOutcomeScenarioDto(
    string ItemCode,
    int CoinLevel,
    double EstimatedPayoutUnits,
    double EstimatedHouseNetUnits);

public sealed record HotOutcomeStatDto(
    string HotItemCode,
    string OutcomeItemCode,
    int HotObservedRounds,
    int OutcomeRounds,
    double OutcomeRatePercent);

public sealed record MarketSideForecastDto(
    double RedProbabilityPercent,
    double GreenProbabilityPercent,
    string? CurrentSide,
    int CurrentStreak,
    int MatchedTransitions,
    double SignalWeightPercent);

public sealed record MarketPredictionResponseDto(
    string Status,
    string Message,
    int? RoundNumber,
    int? CountdownSeconds,
    DateTimeOffset? ObservedAtUtc,
    string? HotItemCode,
    int AnalysisWindow,
    int MatchedSignalRounds,
    HousePerformanceDto HousePerformance,
    IReadOnlyList<HotOutcomeStatDto> HotOutcomeStats,
    MarketSideForecastDto? SideForecast,
    string ComputeDevice,
    long AnalysisDurationMs,
    PredictionDto? Prediction);

public sealed record AiPredictionResponseDto(
    string Status,
    bool IsConfigured,
    string Model,
    PredictionDto? Prediction,
    string? Message);

public sealed record PredictionHistoryItemDto(
    long Id,
    int? RoundNumber,
    string? RoundLocalDate,
    string SourceSerial,
    string ItemCode,
    string Category,
    DateTimeOffset DetectedAtUtc);

public sealed record PredictionContextDto(
    string LocalDate,
    IReadOnlyDictionary<string, double> PayoutMultipliers,
    IReadOnlyDictionary<string, long> HistoricalCounts,
    IReadOnlyDictionary<string, long> TodayCounts,
    IReadOnlyList<PredictionHistoryItemDto> History);

public sealed record SystemEventDeliveryCandidate(
    long EventId,
    string Severity,
    string EventCode,
    string Message,
    JsonElement Details,
    DateTimeOffset OccurredAtUtc);

public sealed record AdminLoginRequest(
    string Username,
    string Password,
    bool RememberMe);

public sealed record AdminSessionDto(
    bool IsAuthenticated,
    bool IsConfigured,
    string? Username);

public sealed record AccessSessionDto(
    bool IsAuthorized,
    string? AccessType,
    string? KeyLabel,
    DateTimeOffset? KeyExpiresAtUtc,
    bool KeyExpired,
    int DeviceLeaseSeconds)
{
    public static AccessSessionDto Denied(int deviceLeaseSeconds, bool expired = false) =>
        new(false, null, null, null, expired, deviceLeaseSeconds);
}

public sealed record AccessKeyLoginRequest(
    string Key,
    string DeviceId,
    string? DeviceName,
    bool RememberMe);

public sealed record CreateAccessKeyRequest(
    string? Label,
    int ValidDays);

public sealed record SetAccessKeyActiveRequest(
    bool IsActive);

public sealed record AccessKeyDto(
    long Id,
    string KeyHint,
    string Label,
    DateTimeOffset ExpiresAtUtc,
    bool IsActive,
    bool IsExpired,
    DateTimeOffset CreatedAtUtc,
    string? DeviceName,
    string? ClientIp,
    DateTimeOffset? LastSeenUtc,
    bool IsOnline);

public sealed record CreatedAccessKeyDto(
    long Id,
    string Key,
    string KeyHint,
    string Label,
    DateTimeOffset ExpiresAtUtc,
    bool IsActive,
    DateTimeOffset CreatedAtUtc);

public sealed record VisitorDto(
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
    bool IsOnline,
    long RequestCount);
