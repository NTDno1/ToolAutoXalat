param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $projectRoot 'runtime'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'processes.json'
$backendDir = Join-Path $projectRoot 'apps\backend\GreedyStats.Api'
$frontendDir = Join-Path $projectRoot 'apps\frontend'
$scannerDir = Join-Path $projectRoot 'services\scanner'

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null

if (Test-Path -LiteralPath $statePath) {
    $oldState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $live = @($oldState.backendPid, $oldState.frontendPid, $oldState.scannerPid) |
        Where-Object { $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
    if ($live.Count -gt 0) {
        throw "Hệ thống đã chạy (PID: $($live -join ', ')). Dùng scripts\stop-all.ps1 trước khi chạy lại."
    }
}

foreach ($port in 5117, 5173) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Port $port đang được PID $($listener.OwningProcess) sử dụng."
    }
}

if (-not $SkipBuild) {
    & dotnet build (Join-Path $backendDir 'GreedyStats.Api.csproj') -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Backend build thất bại.' }

    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules'))) {
        & npm.cmd install --prefix $frontendDir
        if ($LASTEXITCODE -ne 0) { throw 'Frontend npm install thất bại.' }
    }
    & npm.cmd run build --prefix $frontendDir
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build thất bại.' }

    & python -m pip install -r (Join-Path $scannerDir 'requirements.txt') --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Scanner dependency install thất bại.' }
}

$backend = Start-Process -FilePath 'dotnet' `
    -ArgumentList @('run', '-c', 'Release', '--no-build', '--urls', 'http://127.0.0.1:5117') `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'backend.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'backend.stderr.log') `
    -PassThru

$frontend = Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173') `
    -WorkingDirectory $frontendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'frontend.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'frontend.stderr.log') `
    -PassThru

$scanner = Start-Process -FilePath 'python' `
    -ArgumentList @('scanner.py') `
    -WorkingDirectory $scannerDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'scanner.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'scanner.stderr.log') `
    -PassThru

$state = [ordered]@{
    startedAt = (Get-Date).ToString('o')
    backendPid = $backend.Id
    frontendPid = $frontend.Id
    scannerPid = $scanner.Id
    backendUrl = 'http://127.0.0.1:5117'
    frontendUrl = 'http://127.0.0.1:5173'
    emulator = '127.0.0.1:5575'
}
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

$deadline = (Get-Date).AddSeconds(30)
$backendReady = $false
$frontendReady = $false
do {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:5117/health' -TimeoutSec 2
        $backendReady = $health.status -eq 'ok'
    } catch { $backendReady = $false }
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2
        $frontendReady = $response.StatusCode -eq 200
    } catch { $frontendReady = $false }
    if (-not ($backendReady -and $frontendReady)) {
        Start-Sleep -Seconds 1
    }
} while ((Get-Date) -lt $deadline -and -not ($backendReady -and $frontendReady))

if (-not ($backendReady -and $frontendReady)) {
    throw "Dịch vụ không sẵn sàng sau 30 giây. Xem log tại $logDir"
}

Start-Sleep -Seconds 4
$scannerStatus = Invoke-RestMethod -Uri 'http://127.0.0.1:5117/api/scanner/status' -TimeoutSec 5

Write-Host "Backend : http://127.0.0.1:5117 (PID $($backend.Id))"
Write-Host "Frontend: http://127.0.0.1:5173 (PID $($frontend.Id))"
Write-Host "Scanner : PID $($scanner.Id), status=$($scannerStatus.status), online=$($scannerStatus.isOnline)"
Write-Host "Logs    : $logDir"
