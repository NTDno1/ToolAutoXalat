$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$statePath = Join-Path $projectRoot 'runtime\processes.json'

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host 'Không có trạng thái tiến trình của dự án.'
    exit 0
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json

if ($state.scannerTaskName) {
    Stop-ScheduledTask -TaskName $state.scannerTaskName -ErrorAction SilentlyContinue
}

function Stop-ProjectTree([int]$ProcessId) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return }
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProjectTree -ProcessId $child.ProcessId
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

# These PIDs come only from start-all.ps1 and therefore cannot target unrelated
# processes unless the state file was manually altered.
foreach ($processId in @($state.scannerPid, $state.frontendPid, $state.backendPid, $state.zrokAgentPid, $state.cloudflaredPid)) {
    if ($processId) { Stop-ProjectTree -ProcessId ([int]$processId) }
}

Move-Item -LiteralPath $statePath -Destination (Join-Path $projectRoot 'runtime\processes.stopped.json') -Force
Write-Host 'Đã dừng ba tiến trình của Greedy Stats.'
