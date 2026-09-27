using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using System.Text.RegularExpressions;
using GreedyStats.Api.Data;
using GreedyStats.Api.Models;

namespace GreedyStats.Api.Services;

public sealed class PhoneControlService
{
    private static readonly byte[] PngSignature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

    private readonly IConfiguration _configuration;
    private readonly IWebHostEnvironment _environment;
    private readonly IHostApplicationLifetime _lifetime;
    private readonly GreedyDatabase _database;
    private readonly SemaphoreSlim _adbLock = new(1, 1);
    private readonly SemaphoreSlim _bridgeLock = new(1, 1);
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(12) };
    private PreviewBridgeProcess? _bridge;

    public PhoneControlService(
        IConfiguration configuration,
        IWebHostEnvironment environment,
        IHostApplicationLifetime lifetime,
        GreedyDatabase database)
    {
        _configuration = configuration;
        _environment = environment;
        _lifetime = lifetime;
        _database = database;
        _lifetime.ApplicationStopping.Register(StopBridge);
    }

    public async Task<PhoneControlStatusDto> GetStatusAsync(CancellationToken cancellationToken)
    {
        var settings = ResolveSettings();
        if (!settings.Enabled)
        {
            return new PhoneControlStatusDto(false, false, settings.Serial, null, null, null, "Phone control is disabled.");
        }

        var devices = await RunTextAsync(settings with { Serial = null }, ["devices", "-l"], TimeSpan.FromSeconds(5), cancellationToken);
        var deviceLine = devices.Stdout
            .Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .FirstOrDefault(line => settings.Serial is not null
                ? line.StartsWith(settings.Serial, StringComparison.Ordinal)
                : Regex.IsMatch(line, @"^\S+\s+device\b"));
        if (deviceLine is null)
        {
            return new PhoneControlStatusDto(true, false, settings.Serial, null, null, null, "Device is not connected.");
        }
        var serial = deviceLine.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? settings.Serial;
        if (!Regex.IsMatch(deviceLine, @"^\S+\s+device\b"))
        {
            return new PhoneControlStatusDto(true, false, serial, null, null, null, deviceLine);
        }

        var size = await GetScreenSizeAsync(settings, cancellationToken);
        var modelMatch = Regex.Match(deviceLine, @"model:(\S+)");
        var model = modelMatch.Success ? modelMatch.Groups[1].Value : null;
        return new PhoneControlStatusDto(
            true,
            true,
            serial,
            model,
            size.Width,
            size.Height,
            "Connected");
    }

    public async Task<PhoneScreenshot> CaptureImageAsync(CancellationToken cancellationToken)
    {
        var settings = ResolveSettings();
        EnsureEnabled(settings);

        if (settings.CaptureBackend.Equals("scrcpy", StringComparison.OrdinalIgnoreCase))
        {
            return await CaptureViaScrcpyBridgeAsync(settings, cancellationToken);
        }

        try
        {
            var result = await RunBinaryAsync(settings, ["exec-out", "screencap", "-p"], TimeSpan.FromSeconds(8), cancellationToken);
            if (result.Stdout.AsSpan().StartsWith(PngSignature))
            {
                return new PhoneScreenshot(result.Stdout, "image/png");
            }
            if (!string.IsNullOrWhiteSpace(result.Stderr))
            {
                throw new InvalidOperationException(result.Stderr.Trim());
            }
        }
        catch when (!string.IsNullOrWhiteSpace(settings.CaptureBackend))
        {
            if (!settings.CaptureBackend.Equals("scrcpy", StringComparison.OrdinalIgnoreCase))
            {
                throw;
            }
        }

        return await CaptureViaScrcpyBridgeAsync(settings, cancellationToken);
    }

    public async Task TapAsync(PhoneTapRequest request, CancellationToken cancellationToken)
    {
        var settings = ResolveSettings();
        EnsureEnabled(settings);
        var point = await ToScreenPointAsync(settings, request.X, request.Y, cancellationToken);
        await RunTextAsync(settings, ["shell", "input", "tap", point.X.ToString(), point.Y.ToString()], TimeSpan.FromSeconds(4), cancellationToken);
    }

    public async Task SwipeAsync(PhoneSwipeRequest request, CancellationToken cancellationToken)
    {
        var settings = ResolveSettings();
        EnsureEnabled(settings);
        var start = await ToScreenPointAsync(settings, request.StartX, request.StartY, cancellationToken);
        var end = await ToScreenPointAsync(settings, request.EndX, request.EndY, cancellationToken);
        var duration = Math.Clamp(request.DurationMs, 80, 1200);
        await RunTextAsync(settings, [
            "shell", "input", "swipe",
            start.X.ToString(), start.Y.ToString(),
            end.X.ToString(), end.Y.ToString(),
            duration.ToString()
        ], TimeSpan.FromSeconds(5), cancellationToken);
    }

    public async Task KeyAsync(PhoneKeyRequest request, CancellationToken cancellationToken)
    {
        var settings = ResolveSettings();
        EnsureEnabled(settings);
        if (request.KeyCode is < 1 or > 300)
        {
            throw new ArgumentOutOfRangeException(nameof(request.KeyCode), "KeyCode is out of range.");
        }
        await RunTextAsync(settings, ["shell", "input", "keyevent", request.KeyCode.ToString()], TimeSpan.FromSeconds(4), cancellationToken);
    }

    public async Task TapBetItemAsync(
        string itemCode,
        int tapCount,
        CancellationToken cancellationToken)
    {
        await PlaceBetsAsync(
            [new AutoPlayBetDto(itemCode, itemCode, tapCount, tapCount, 0, 0, 0)],
            2,
            cancellationToken);
    }

    public async Task<PhoneBetPlacementResult> PlaceBetsAsync(
        IReadOnlyList<AutoPlayBetDto> bets,
        int chipValue,
        CancellationToken cancellationToken,
        int? expectedRound = null)
    {
        if (bets.Count is < 1 or > 3)
        {
            throw new InvalidOperationException("Auto-play requires one to three bet targets.");
        }
        var liveTest = _configuration.GetValue("AutoPlay:LiveTestModeEnabled", false);
        if (liveTest &&
            (chipValue != 2 || bets.Count != 1 || bets[0].TapCount != 1 || bets[0].StakeUnits != 2))
        {
            throw new InvalidOperationException("Live test is limited to exactly one 2-coin bet.");
        }
        var settings = ResolveSettings();
        EnsureEnabled(settings);
        if (liveTest)
        {
            if (expectedRound is null)
                throw new InvalidOperationException("Live test requires the observed round number.");
            var before = await ProbeParticipationDialogAsync(settings, cancellationToken);
            if (before.DialogVisible)
                throw new InvalidOperationException("Participation dialog was already open before placing the bet.");
        }
        var chipSection = _configuration.GetSection($"AutoPlay:ChipTargets:{chipValue}");
        var chipX = chipSection.GetValue<double?>("X");
        var chipY = chipSection.GetValue<double?>("Y");
        if (chipX is null || chipY is null || chipX is < 0 or > 1 || chipY is < 0 or > 1)
        {
            throw new InvalidOperationException($"Auto-play chip target is not configured for {chipValue} xu.");
        }

        var chipPoint = await ToScreenPointAsync(settings, chipX.Value, chipY.Value, cancellationToken);
        await RunTextAsync(
            settings,
            ["shell", "input", "tap", chipPoint.X.ToString(), chipPoint.Y.ToString()],
            TimeSpan.FromSeconds(4),
            cancellationToken);
        await Task.Delay(120, cancellationToken);
        foreach (var bet in bets)
        {
            var normalizedCode = bet.ItemCode.Trim().ToUpperInvariant();
            var targetSection = _configuration.GetSection($"AutoPlay:ItemTargets:{normalizedCode}");
            var x = targetSection.GetValue<double?>("X");
            var y = targetSection.GetValue<double?>("Y");
            if (x is null || y is null || x is < 0 or > 1 || y is < 0 or > 1)
            {
                throw new InvalidOperationException($"Auto-play target is not configured for {normalizedCode}.");
            }
            var point = await ToScreenPointAsync(settings, x.Value, y.Value, cancellationToken);
            var safeTapCount = Math.Clamp(bet.TapCount, 1, 10);
            for (var index = 0; index < safeTapCount; index++)
            {
                await RunTextAsync(
                    settings,
                    ["shell", "input", "tap", point.X.ToString(), point.Y.ToString()],
                    TimeSpan.FromSeconds(4),
                    cancellationToken);
                await Task.Delay(120, cancellationToken);
            }
        }
        if (!liveTest) return new PhoneBetPlacementResult(0, "NO_TEST_DIALOG");

        var tapCompletedAtUtc = DateTimeOffset.UtcNow;
        ParticipationDialogProbe? dialog = null;
        for (var attempt = 0; attempt < 4; attempt++)
        {
            await Task.Delay(300, cancellationToken);
            var observed = await ProbeParticipationDialogAsync(settings, cancellationToken);
            if (observed.ObservedAtUtc <= tapCompletedAtUtc) continue;
            dialog = observed;
            if (dialog.DialogVisible || attempt >= 1) break;
        }
        if (dialog is null || dialog.ObservedAtUtc <= tapCompletedAtUtc)
            throw new InvalidOperationException("No fresh scanner frame after the item tap.");
        if (!dialog.DialogVisible) return new PhoneBetPlacementResult(0, "NO_DIALOG");
        if (dialog.CostDiamonds != 2 ||
            (dialog.RoundNumber is not null && dialog.RoundNumber != expectedRound) ||
            !dialog.ConfirmButtonVisible)
        {
            await TapParticipationButtonAsync(settings, confirm: false, cancellationToken);
            throw new InvalidOperationException(
                $"Participation dialog rejected: cost={dialog.CostDiamonds?.ToString() ?? "?"} diamonds, " +
                $"round={dialog.RoundNumber?.ToString() ?? "?"}, confirm={dialog.ConfirmButtonVisible}.");
        }
        var current = await _database.GetScannerStatusAsync(cancellationToken);
        if (current.ActiveRound != expectedRound || current.CountdownSeconds is null or < 8 ||
            current.CountdownObservedAtUtc is null ||
            DateTimeOffset.UtcNow - current.CountdownObservedAtUtc > TimeSpan.FromSeconds(6))
        {
            await TapParticipationButtonAsync(settings, confirm: false, cancellationToken);
            throw new InvalidOperationException("Round or countdown changed before the 2-diamond confirmation.");
        }
        await TapParticipationButtonAsync(settings, confirm: true, cancellationToken);
        return new PhoneBetPlacementResult(2, "TAPPED_2_DIAMOND_CONFIRM");
    }

    private async Task TapParticipationButtonAsync(
        PhoneControlSettings settings, bool confirm, CancellationToken cancellationToken)
    {
        var point = await ToScreenPointAsync(settings, confirm ? 480d / 720d : 240d / 720d,
            900d / 1600d, cancellationToken);
        await RunTextAsync(settings,
            ["shell", "input", "tap", point.X.ToString(), point.Y.ToString()],
            TimeSpan.FromSeconds(4), cancellationToken);
    }

    private async Task<ParticipationDialogProbe> ProbeParticipationDialogAsync(
        PhoneControlSettings settings, CancellationToken cancellationToken)
    {
        var probePath = Path.Combine(Path.GetDirectoryName(settings.ScannerConfigPath)!, "participation_probe.py");
        if (!File.Exists(probePath))
            throw new FileNotFoundException("Participation probe is missing from the production snapshot.", probePath);
        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(TimeSpan.FromSeconds(10));
        using var process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = settings.PythonPath,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            }
        };
        process.StartInfo.ArgumentList.Add(probePath);
        process.StartInfo.ArgumentList.Add("--config");
        process.StartInfo.ArgumentList.Add(settings.ScannerConfigPath);
        process.Start();
        var outputTask = process.StandardOutput.ReadToEndAsync(timeoutCts.Token);
        var errorTask = process.StandardError.ReadToEndAsync(timeoutCts.Token);
        try
        {
            await process.WaitForExitAsync(timeoutCts.Token);
            var output = await outputTask;
            var error = await errorTask;
            if (process.ExitCode != 0)
                throw new InvalidOperationException($"Participation probe failed: {error.Trim()}");
            return JsonSerializer.Deserialize<ParticipationDialogProbe>(output,
                new JsonSerializerOptions(JsonSerializerDefaults.Web))
                ?? throw new InvalidOperationException("Participation probe returned no data.");
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            TryKill(process);
            throw new TimeoutException("Participation probe timed out.");
        }
    }

    public void StopPreview()
    {
        StopBridge();
    }

    private async Task<(int X, int Y)> ToScreenPointAsync(
        PhoneControlSettings settings,
        double normalizedX,
        double normalizedY,
        CancellationToken cancellationToken)
    {
        var size = await GetScreenSizeAsync(settings, cancellationToken);
        var x = (int)Math.Round(Math.Clamp(normalizedX, 0, 1) * Math.Max(1, size.Width - 1));
        var y = (int)Math.Round(Math.Clamp(normalizedY, 0, 1) * Math.Max(1, size.Height - 1));
        return (x, y);
    }

    private async Task<(int Width, int Height)> GetScreenSizeAsync(
        PhoneControlSettings settings,
        CancellationToken cancellationToken)
    {
        var output = await RunTextAsync(settings, ["shell", "wm", "size"], TimeSpan.FromSeconds(5), cancellationToken);
        var match = Regex.Match(output.Stdout, @"(\d+)x(\d+)");
        if (!match.Success)
        {
            throw new InvalidOperationException("Cannot read phone screen size.");
        }
        return (int.Parse(match.Groups[1].Value), int.Parse(match.Groups[2].Value));
    }

    private async Task<PhoneScreenshot> CaptureViaScrcpyBridgeAsync(
        PhoneControlSettings settings,
        CancellationToken cancellationToken)
    {
        var bridge = await EnsureBridgeAsync(settings, cancellationToken);
        using var response = await _httpClient.GetAsync(
            $"http://127.0.0.1:{bridge.Port}/screenshot?ts={DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}",
            cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
            var error = await response.Content.ReadAsStringAsync(cancellationToken);
            StopBridge();
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(error)
                ? $"scrcpy preview bridge returned {(int)response.StatusCode}."
                : error);
        }
        return new PhoneScreenshot(
            await response.Content.ReadAsByteArrayAsync(cancellationToken),
            response.Content.Headers.ContentType?.MediaType ?? "image/jpeg");
    }

    private async Task<PreviewBridgeProcess> EnsureBridgeAsync(
        PhoneControlSettings settings,
        CancellationToken cancellationToken)
    {
        await _bridgeLock.WaitAsync(cancellationToken);
        try
        {
            if (_bridge is { Process.HasExited: false } current &&
                string.Equals(current.ConfigPath, settings.ScannerConfigPath, StringComparison.OrdinalIgnoreCase))
            {
                return current;
            }

            StopBridge();
            var scriptPath = Path.GetFullPath("../../../services/scanner/phone_preview_server.py", _environment.ContentRootPath);
            if (!File.Exists(scriptPath))
            {
                throw new InvalidOperationException($"Phone preview bridge not found: {scriptPath}");
            }
            if (!File.Exists(settings.ScannerConfigPath))
            {
                throw new InvalidOperationException($"Scanner config not found: {settings.ScannerConfigPath}");
            }

            var port = FindAvailablePort();
            var process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = settings.PythonPath,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                },
                EnableRaisingEvents = true
            };
            foreach (var argument in new[]
            {
                scriptPath,
                "--config", settings.ScannerConfigPath,
                "--host", "127.0.0.1",
                "--port", port.ToString(),
                "--max-size", settings.PreviewMaxSize.ToString(),
                "--max-fps", settings.PreviewMaxFps.ToString(),
                "--bit-rate", settings.PreviewBitRate.ToString(),
                "--jpeg-quality", settings.PreviewJpegQuality.ToString(),
                "--jpeg-max-width", settings.PreviewJpegMaxWidth.ToString(),
                "--jpeg-max-height", settings.PreviewJpegMaxHeight.ToString()
            })
            {
                process.StartInfo.ArgumentList.Add(argument);
            }

            try
            {
                process.Start();
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException($"Cannot start phone preview bridge with {settings.PythonPath}: {ex.Message}", ex);
            }
            _ = Task.Run(() => DrainAsync(process.StandardOutput));
            _ = Task.Run(() => DrainAsync(process.StandardError));

            var bridge = new PreviewBridgeProcess(process, port, settings.ScannerConfigPath);
            await WaitForBridgeAsync(bridge, cancellationToken);
            _bridge = bridge;
            return bridge;
        }
        finally
        {
            _bridgeLock.Release();
        }
    }

    private async Task WaitForBridgeAsync(PreviewBridgeProcess bridge, CancellationToken cancellationToken)
    {
        var deadline = DateTimeOffset.UtcNow.AddSeconds(18);
        Exception? lastError = null;
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (bridge.Process.HasExited)
            {
                throw new InvalidOperationException($"Phone preview bridge stopped with code {bridge.Process.ExitCode}.");
            }
            try
            {
                using var response = await _httpClient.GetAsync($"http://127.0.0.1:{bridge.Port}/health", cancellationToken);
                if (response.IsSuccessStatusCode) return;
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
            {
                lastError = ex;
            }
            await Task.Delay(250, cancellationToken);
        }
        TryKill(bridge.Process);
        throw new TimeoutException($"Phone preview bridge did not start in time. {lastError?.Message}");
    }

    private static async Task DrainAsync(StreamReader reader)
    {
        try
        {
            while (await reader.ReadLineAsync() is not null)
            {
                // Keep the redirected pipe drained.
            }
        }
        catch
        {
            // The bridge process is allowed to exit while the backend is stopping.
        }
    }

    private void StopBridge()
    {
        var bridge = Interlocked.Exchange(ref _bridge, null);
        if (bridge is null) return;
        TryKill(bridge.Process);
        bridge.Process.Dispose();
    }

    private static int FindAvailablePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            return ((IPEndPoint)listener.LocalEndpoint).Port;
        }
        finally
        {
            listener.Stop();
        }
    }

    private PhoneControlSettings ResolveSettings()
    {
        var scannerConfigPath = _configuration["PhoneControl:ScannerConfigPath"] ??
            Path.GetFullPath("../../../services/scanner/config.redmi-k30.json", _environment.ContentRootPath);
        if (!Path.IsPathRooted(scannerConfigPath))
        {
            scannerConfigPath = Path.GetFullPath(scannerConfigPath, _environment.ContentRootPath);
        }

        string? adbPath = _configuration["PhoneControl:AdbPath"];
        string? serial = _configuration["PhoneControl:Serial"];
        string? captureBackend = _configuration["PhoneControl:CaptureBackend"];
        string? pythonPath = _configuration["PhoneControl:PythonPath"];
        if (File.Exists(scannerConfigPath))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(scannerConfigPath));
            if (document.RootElement.TryGetProperty("source", out var source))
            {
                if (string.IsNullOrWhiteSpace(adbPath) &&
                    source.TryGetProperty("adb_path", out var adbPathElement))
                {
                    adbPath = adbPathElement.GetString();
                }
                if (string.IsNullOrWhiteSpace(serial) &&
                    source.TryGetProperty("serial", out var serialElement))
                {
                    serial = serialElement.GetString();
                }
                if (string.IsNullOrWhiteSpace(captureBackend) &&
                    source.TryGetProperty("capture_backend", out var captureBackendElement))
                {
                    captureBackend = captureBackendElement.GetString();
                }
            }
        }

        adbPath = string.IsNullOrWhiteSpace(adbPath) ? "adb" : adbPath.Trim();
        serial = string.IsNullOrWhiteSpace(serial) ? null : serial.Trim();
        captureBackend = string.IsNullOrWhiteSpace(captureBackend) ? "screencap" : captureBackend.Trim();
        pythonPath = string.IsNullOrWhiteSpace(pythonPath) ? "python" : pythonPath.Trim();
        var enabled = _configuration.GetValue("PhoneControl:Enabled", true);
        var previewMaxSize = Math.Clamp(_configuration.GetValue("PhoneControl:PreviewMaxSize", 960), 320, 1600);
        var previewMaxFps = Math.Clamp(_configuration.GetValue("PhoneControl:PreviewMaxFps", 6), 1, 20);
        var previewBitRate = Math.Clamp(_configuration.GetValue("PhoneControl:PreviewBitRate", 1500000), 100000, 8000000);
        var previewJpegQuality = Math.Clamp(_configuration.GetValue("PhoneControl:PreviewJpegQuality", 68), 35, 90);
        var previewJpegMaxWidth = Math.Clamp(_configuration.GetValue("PhoneControl:PreviewJpegMaxWidth", 540), 240, 1600);
        var previewJpegMaxHeight = Math.Clamp(_configuration.GetValue("PhoneControl:PreviewJpegMaxHeight", 1200), 360, 2400);
        return new PhoneControlSettings(
            enabled,
            adbPath,
            serial,
            scannerConfigPath,
            captureBackend,
            pythonPath,
            previewMaxSize,
            previewMaxFps,
            previewBitRate,
            previewJpegQuality,
            previewJpegMaxWidth,
            previewJpegMaxHeight);
    }

    private static void EnsureEnabled(PhoneControlSettings settings)
    {
        if (!settings.Enabled)
        {
            throw new InvalidOperationException("Phone control is disabled.");
        }
        if (string.IsNullOrWhiteSpace(settings.Serial))
        {
            throw new InvalidOperationException("Phone serial is not configured.");
        }
    }

    private async Task<(string Stdout, string Stderr)> RunTextAsync(
        PhoneControlSettings settings,
        IReadOnlyList<string> arguments,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        var result = await RunProcessAsync(settings, arguments, timeout, cancellationToken);
        return (System.Text.Encoding.UTF8.GetString(result.Stdout), result.Stderr);
    }

    private Task<(byte[] Stdout, string Stderr)> RunBinaryAsync(
        PhoneControlSettings settings,
        IReadOnlyList<string> arguments,
        TimeSpan timeout,
        CancellationToken cancellationToken) =>
        RunProcessAsync(settings, arguments, timeout, cancellationToken);

    private async Task<(byte[] Stdout, string Stderr)> RunProcessAsync(
        PhoneControlSettings settings,
        IReadOnlyList<string> arguments,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        await _adbLock.WaitAsync(cancellationToken);
        try
        {
            var fullArguments = new List<string>();
            if (!string.IsNullOrWhiteSpace(settings.Serial))
            {
                fullArguments.Add("-s");
                fullArguments.Add(settings.Serial!);
            }
            fullArguments.AddRange(arguments);

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(timeout);
            using var process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = settings.AdbPath,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                }
            };
            foreach (var argument in fullArguments)
            {
                process.StartInfo.ArgumentList.Add(argument);
            }

            process.Start();
            using var stdout = new MemoryStream();
            var stdoutTask = process.StandardOutput.BaseStream.CopyToAsync(stdout, timeoutCts.Token);
            var stderrTask = process.StandardError.ReadToEndAsync(timeoutCts.Token);
            try
            {
                await process.WaitForExitAsync(timeoutCts.Token);
                await stdoutTask;
                var stderr = await stderrTask;
                if (process.ExitCode != 0)
                {
                    throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr)
                        ? $"ADB exited with code {process.ExitCode}."
                        : stderr.Trim());
                }
                return (stdout.ToArray(), stderr);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                TryKill(process);
                throw new TimeoutException("ADB command timed out.");
            }
        }
        finally
        {
            _adbLock.Release();
        }
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        catch
        {
            // The process may exit between the timeout and the kill attempt.
        }
    }

    public sealed record PhoneScreenshot(byte[] Bytes, string ContentType);
    public sealed record PhoneBetPlacementResult(int ParticipationDiamonds, string ParticipationStatus);
    private sealed record ParticipationDialogProbe(bool DialogVisible, int? CostDiamonds,
        int? RoundNumber, bool ConfirmButtonVisible, string RawCostText, DateTimeOffset ObservedAtUtc);

    private sealed record PhoneControlSettings(
        bool Enabled,
        string AdbPath,
        string? Serial,
        string ScannerConfigPath,
        string CaptureBackend,
        string PythonPath,
        int PreviewMaxSize,
        int PreviewMaxFps,
        int PreviewBitRate,
        int PreviewJpegQuality,
        int PreviewJpegMaxWidth,
        int PreviewJpegMaxHeight);

    private sealed record PreviewBridgeProcess(Process Process, int Port, string ConfigPath);
}
