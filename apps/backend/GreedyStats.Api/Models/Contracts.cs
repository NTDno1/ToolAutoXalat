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

public sealed record ScannerStatusDto(
    string Status,
    bool IsOnline,
    DateTimeOffset? LastHeartbeatUtc,
    long? LastResultId,
    IReadOnlyList<string> LastSequence,
    int OfflineAfterSeconds,
    string? SourceSerial,
    int? CurrentRound);

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
    string Password);

public sealed record AdminSessionDto(
    bool IsAuthenticated,
    bool IsConfigured,
    string? Username);
