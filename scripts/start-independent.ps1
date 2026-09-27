param(
    [switch]$SkipBuild,
    [string]$ScannerConfig = '',
    [ValidatePattern('^[^\s"]+$')][string]$Device = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $projectRoot 'runtime'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'processes.json'
$backendDir = Join-Path $projectRoot 'apps\backend\GreedyStats.Api'
$frontendDir = Join-Path $projectRoot 'apps\frontend'
$scannerDir = Join-Path $projectRoot 'services\scanner'

if (-not $ScannerConfig) { $ScannerConfig = Join-Path $scannerDir 'config.redmi-k30.json' }
$ScannerConfig = (Resolve-Path -LiteralPath $ScannerConfig).Path
$scannerSettings = Get-Content -LiteralPath $ScannerConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Device -and $scannerSettings.source.type -eq 'adb') { $Device = $scannerSettings.source.serial }

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null

$logs = @{
    backendOut = Join-Path $logDir 'backend.stdout.log'
    backendErr = Join-Path $logDir 'backend.stderr.log'
    frontendOut = Join-Path $logDir 'frontend.stdout.log'
    frontendErr = Join-Path $logDir 'frontend.stderr.log'
    scannerOut = Join-Path $logDir 'scanner.stdout.log'
    scannerErr = Join-Path $logDir 'scanner.stderr.log'
}
foreach ($path in $logs.Values) {
    if (Test-Path -LiteralPath $path) {
        Clear-Content -LiteralPath $path -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType File -Path $path -Force | Out-Null
    }
}

foreach ($port in 5117, 5173) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Port $port is still in use by PID $($listener.OwningProcess). Run scripts\stop-all.ps1 first."
    }
}

if (-not $SkipBuild) {
    & dotnet build (Join-Path $backendDir 'GreedyStats.Api.csproj') -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Backend build failed.' }

    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules'))) {
        & npm.cmd install --prefix $frontendDir
        if ($LASTEXITCODE -ne 0) { throw 'Frontend npm install failed.' }
    }
    & npm.cmd run build --prefix $frontendDir
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}

$backend = Start-Process -FilePath 'dotnet' `
    -ArgumentList @('run', '-c', 'Release', '--no-build', '--urls', 'http://127.0.0.1:5117') `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logs.backendOut `
    -RedirectStandardError $logs.backendErr `
    -PassThru

$frontend = Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'preview', '--', '--host', '127.0.0.1', '--port', '5173') `
    -WorkingDirectory $frontendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logs.frontendOut `
    -RedirectStandardError $logs.frontendErr `
    -PassThru

$pythonPath = 'C:\Users\datt\AppData\Local\Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
}
$scannerArgs = @((Join-Path $scannerDir 'scanner.py'), '--config', $ScannerConfig)
if ($Device) { $scannerArgs += @('--device', $Device) }
$scanner = Start-Process -FilePath $pythonPath `
    -ArgumentList $scannerArgs `
    -WorkingDirectory $scannerDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logs.scannerOut `
    -RedirectStandardError $logs.scannerErr `
    -PassThru

$tunnel = & (Join-Path $PSScriptRoot 'start-zrok-tunnel.ps1') -TargetPort 5173

$state = [ordered]@{
    startedAt = (Get-Date).ToString('o')
    backendPid = $backend.Id
    frontendPid = $frontend.Id
    scannerPid = $scanner.Id
    backendUrl = 'http://127.0.0.1:5117'
    frontendUrl = 'http://127.0.0.1:5173'
    tunnelProvider = $tunnel.Provider
    publicUrl = $tunnel.PublicUrl
    zrokUrl = $tunnel.PublicUrl
    zrokShareName = $tunnel.ShareName
    zrokAgentPid = $tunnel.AgentPid
    zrokLog = $tunnel.AgentLog
    scannerSourceSerial = $Device
    scannerConfigPath = $ScannerConfig
    startMode = 'independent-no-phone-preflight'
}
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

$deadline = (Get-Date).AddSeconds(45)
$backendReady = $false
$frontendReady = $false
do {
    try {
        $backendReady = (Invoke-RestMethod -Uri 'http://127.0.0.1:5117/health' -TimeoutSec 2).status -eq 'ok'
    } catch {
        $backendReady = $false
    }
    try {
        $frontendReady = (Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
    } catch {
        $frontendReady = $false
    }
    if (-not ($backendReady -and $frontendReady)) {
        Start-Sleep -Seconds 1
    }
} while ((Get-Date) -lt $deadline -and -not ($backendReady -and $frontendReady))

[pscustomobject]@{
    backendPid = $backend.Id
    frontendPid = $frontend.Id
    scannerPid = $scanner.Id
    backendReady = $backendReady
    frontendReady = $frontendReady
    tunnelProvider = $tunnel.Provider
    publicUrl = $tunnel.PublicUrl
}
