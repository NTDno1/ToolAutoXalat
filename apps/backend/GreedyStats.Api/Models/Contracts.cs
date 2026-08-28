using System.Text.Json;

namespace GreedyStats.Api.Models;

public sealed record ResultDto(
    long Id,
    string ItemCode,
    string ItemName,
    string Category,
    DateTimeOffset DetectedAtUtc,
    double Confidence,
    IReadOnlyList<string> Sequence,
    string DetectionReason,
    string? CapturePath);

public sealed record PageDto<T>(
    IReadOnlyList<T> Items,
    int Page,
    int PageSize,
    long TotalItems,
    int TotalPages);

public sealed record StreakRunDto(
    string Category,
    int Length,
    long StartResultId,
    long EndResultId,
    DateTimeOffset StartedAtUtc,
    DateTimeOffset EndedAtUtc);

public sealed record TodayStatsDto(
    string LocalDate,
    long TotalResults,
    long VegetableCount,
    long MeatCount,
    int CurrentVegetableStreak,
    int CurrentMeatStreak,
    int LongestVegetableStreak,
    int LongestMeatStreak,
    IReadOnlyDictionary<string, long> ItemCounts,
    IReadOnlyList<StreakRunDto> VegetableRuns,
    IReadOnlyList<StreakRunDto> MeatRuns,
    ResultDto? LatestResult);

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
    int OfflineAfterSeconds);

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
