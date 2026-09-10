$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$scannerDir = Join-Path $projectRoot 'services\scanner'
$runtimeDir = Join-Path $projectRoot 'runtime'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'processes.json'
$scannerPath = Join-Path $scannerDir 'scanner.py'
$taskName = 'ToolAutoXalat-Scanner'
$scannerConfig = Get-Content -LiteralPath (Join-Path $scannerDir 'config.json') -Raw | ConvertFrom-Json
$expectedSourceSerial = '{0}:{1}' -f $scannerConfig.emulator.adb_host, $scannerConfig.emulator.adb_port

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
} else {
    $state = [pscustomobject]@{}
}

if ($state.scannerPid) {
    $oldScanner = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.scannerPid)" -ErrorAction SilentlyContinue
    if ($oldScanner) {
        $isPython = $oldScanner.Name -match '^python(?:w)?\.exe$'
        $isScannerCommand = $oldScanner.CommandLine -and (
            $oldScanner.CommandLine.IndexOf(
                $scannerPath,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0
        )
        if (-not ($isPython -and $isScannerCommand)) {
            throw "Stored scanner PID $($state.scannerPid) belongs to an unrelated process."
        }
        Stop-Process -Id $oldScanner.ProcessId -Force
    }
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

# Stop only scanner.py processes that belong to this exact workspace.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match '^python(?:w)?\.exe$' -and
        $_.CommandLine -and
        $_.CommandLine.IndexOf($scannerPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$pythonPath = (Get-Command python -ErrorAction Stop).Source
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ('"{0}"' -f $scannerPath) `
    -WorkingDirectory $scannerDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -Hidden
$principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Background BlueStacks scanner for ToolAutoXalat; no UI input.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$deadline = (Get-Date).AddSeconds(30)
$online = $false
$scanner = $null
do {
    $scanner = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^python(?:w)?\.exe$' -and
            $_.CommandLine -and
            $_.CommandLine.IndexOf($scannerPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        Select-Object -First 1
    try {
        $status = Invoke-RestMethod -Uri 'http://127.0.0.1:5117/api/scanner/status' -TimeoutSec 2
        $heartbeatIsCurrent = $false
        if ($scanner -and $status.lastHeartbeatUtc) {
            $heartbeatUtc = [DateTimeOffset]::Parse($status.lastHeartbeatUtc).UtcDateTime
            $processStartedUtc = ([DateTime]$scanner.CreationDate).ToUniversalTime()
            $heartbeatIsCurrent = $heartbeatUtc -ge $processStartedUtc.AddSeconds(-1)
        }
        $online = $scanner -and $status.isOnline -and $heartbeatIsCurrent -and `
            $status.sourceSerial -eq $expectedSourceSerial
    } catch { $online = $false }
    if (-not $online) { Start-Sleep -Milliseconds 500 }
} while ((Get-Date) -lt $deadline -and -not $online)

if (-not $online) { throw "Scanner task did not become online. See $logDir." }

$state | Add-Member -NotePropertyName scannerPid -NotePropertyValue ([int]$scanner.ProcessId) -Force
$state | Add-Member -NotePropertyName scannerTaskName -NotePropertyValue $taskName -Force
$state | Add-Member -NotePropertyName scannerRestartedAt -NotePropertyValue (Get-Date).ToString('o') -Force
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Scanner ready: source=$($status.sourceSerial), status=$($status.status), PID=$($scanner.ProcessId), task=$taskName"
