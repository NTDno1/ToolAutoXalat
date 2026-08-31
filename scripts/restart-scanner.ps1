$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$scannerDir = Join-Path $projectRoot 'services\scanner'
$runtimeDir = Join-Path $projectRoot 'runtime'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'processes.json'

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
} else {
    $state = [pscustomobject]@{}
}

if ($state.scannerPid) {
    $oldScanner = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.scannerPid)" -ErrorAction SilentlyContinue
    if ($oldScanner) {
        $isPython = $oldScanner.Name -match '^python(?:w)?\.exe$'
        $isScannerCommand = $oldScanner.CommandLine -match '(?:^|[\s"])scanner\.py(?:[\s"]|$)'
        if (-not ($isPython -and $isScannerCommand)) {
            throw "Stored scanner PID $($state.scannerPid) belongs to an unrelated process."
        }
        Stop-Process -Id $oldScanner.ProcessId -Force
    }
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null
$scanner = Start-Process -FilePath 'python' `
    -ArgumentList @('scanner.py') `
    -WorkingDirectory $scannerDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'scanner.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'scanner.stderr.log') `
    -PassThru

$state | Add-Member -NotePropertyName scannerPid -NotePropertyValue $scanner.Id -Force
$state | Add-Member -NotePropertyName scannerRestartedAt -NotePropertyValue (Get-Date).ToString('o') -Force
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

$deadline = (Get-Date).AddSeconds(30)
$online = $false
do {
    if (-not (Get-Process -Id $scanner.Id -ErrorAction SilentlyContinue)) {
        throw "Scanner exited during startup. See $logDir."
    }
    try {
        $status = Invoke-RestMethod -Uri 'http://127.0.0.1:5117/api/scanner/status' -TimeoutSec 2
        $online = $status.isOnline -and $status.sourceSerial -eq '127.0.0.1:5555'
    } catch { $online = $false }
    if (-not $online) { Start-Sleep -Milliseconds 500 }
} while ((Get-Date) -lt $deadline -and -not $online)

if (-not $online) { throw "Scanner did not become online. See $logDir." }
Write-Host "Scanner ready: source=$($status.sourceSerial), status=$($status.status), PID=$($scanner.Id)"
