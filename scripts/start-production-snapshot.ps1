param(
    [string]$ScannerConfig = '',
    [ValidatePattern('^[^\s"]+$')][string]$Device = '',
    [switch]$LiveTestMode,
    [bool]$OpenBrowser = $true
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $projectRoot 'runtime'
$releasesDir = Join-Path $runtimeDir 'releases'
$statePath = Join-Path $runtimeDir 'processes.json'
$backendProject = Join-Path $projectRoot 'apps\backend\GreedyStats.Api\GreedyStats.Api.csproj'
$frontendSource = Join-Path $projectRoot 'apps\frontend'
$scannerSource = Join-Path $projectRoot 'services\scanner'
$databasePath = Join-Path $projectRoot 'data\greedy_stats.db'
$taskName = 'ToolAutoXalat-ProductionScanner'

if (-not $ScannerConfig) { $ScannerConfig = Join-Path $scannerSource 'config.redmi-k30.json' }
$ScannerConfig = (Resolve-Path -LiteralPath $ScannerConfig).Path

New-Item -ItemType Directory -Force -Path $runtimeDir, $releasesDir | Out-Null

if (Test-Path -LiteralPath $statePath) {
    $existing = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $liveProcesses = @($existing.backendPid, $existing.frontendPid, $existing.scannerPid, $existing.cloudflaredPid) |
        Where-Object { $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
    if ($liveProcesses.Count -gt 0) {
        Write-Host ''
        Write-Host 'HE THONG DANG CHAY - KHONG KHOI DONG TRUNG.' -ForegroundColor Yellow
        if ($existing.publicUrl) {
            Write-Host "Cloudflare: $($existing.publicUrl)" -ForegroundColor Cyan
            Set-Content -LiteralPath (Join-Path $runtimeDir 'PUBLIC_URL.txt') -Value $existing.publicUrl -Encoding UTF8
            try { Set-Clipboard -Value $existing.publicUrl } catch {}
            if ($OpenBrowser) { Start-Process $existing.publicUrl }
        }
        return
    }
}

foreach ($port in 5117, 5173) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) { throw "Port $port dang duoc PID $($listener.OwningProcess) su dung." }
}

$dotnetPath = (Get-Command dotnet -ErrorAction Stop).Source
$npmPath = (Get-Command npm.cmd -ErrorAction Stop).Source
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$pythonPath = (Get-Command python -ErrorAction Stop).Source
$cloudflaredPath = (Get-Command cloudflared.exe -ErrorAction Stop).Source

$releaseId = Get-Date -Format 'yyyyMMdd-HHmmss'
$releaseRoot = Join-Path $releasesDir $releaseId
$backendRelease = Join-Path $releaseRoot 'apps\backend\GreedyStats.Api'
$frontendRelease = Join-Path $releaseRoot 'apps\frontend\dist'
$scannerRelease = Join-Path $releaseRoot 'services\scanner'
$releaseScripts = Join-Path $releaseRoot 'scripts'
$logDir = Join-Path $runtimeDir "logs\production-$releaseId"
New-Item -ItemType Directory -Force -Path $backendRelease, $frontendRelease, $scannerRelease, $releaseScripts, $logDir | Out-Null

Write-Host '[1/6] Publishing backend Release snapshot...' -ForegroundColor Cyan
& $dotnetPath publish $backendProject -c Release -o $backendRelease --nologo
if ($LASTEXITCODE -ne 0) { throw 'dotnet publish failed.' }

Write-Host '[2/6] Building frontend production assets...' -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath (Join-Path $frontendSource 'node_modules'))) {
    & $npmPath ci --prefix $frontendSource
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
}
& $npmPath run build --prefix $frontendSource
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Copy-Item -Path (Join-Path $frontendSource 'dist\*') -Destination $frontendRelease -Recurse -Force

Write-Host '[3/6] Freezing scanner, assets and configuration...' -ForegroundColor Cyan
Copy-Item -LiteralPath (Join-Path $scannerSource 'scanner.py') -Destination $scannerRelease -Force
Copy-Item -LiteralPath (Join-Path $scannerSource 'phone_preview_server.py') -Destination $scannerRelease -Force
Copy-Item -LiteralPath (Join-Path $scannerSource 'participation_probe.py') -Destination $scannerRelease -Force
Copy-Item -LiteralPath (Join-Path $scannerSource 'requirements.txt') -Destination $scannerRelease -Force
Copy-Item -LiteralPath (Join-Path $scannerSource 'src') -Destination $scannerRelease -Recurse -Force
Copy-Item -LiteralPath (Join-Path $scannerSource 'assets') -Destination $scannerRelease -Recurse -Force
$releaseConfigPath = Join-Path $scannerRelease (Split-Path -Leaf $ScannerConfig)
Copy-Item -LiteralPath $ScannerConfig -Destination $releaseConfigPath -Force
$releaseConfig = Get-Content -LiteralPath $releaseConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$releaseConfig.paths.database = $databasePath
$releaseConfig.paths.templates = Join-Path $scannerRelease 'assets\templates'
$releaseConfig.paths.captures = Join-Path $projectRoot 'data\captures'
$releaseConfig.paths.logs = $logDir
if ($LiveTestMode) {
    $releaseConfig.paths | Add-Member -NotePropertyName live_frame -NotePropertyValue (Join-Path $runtimeDir "live-frame-$releaseId.jpg") -Force
}
$releaseConfig.backend.base_url = 'http://127.0.0.1:5117'
$releaseConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $releaseConfigPath -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'production-web-server.mjs') -Destination $releaseScripts -Force

$sourceCommit = 'unavailable'
try { $sourceCommit = (& git -C $projectRoot rev-parse --short HEAD 2>$null).Trim() } catch {}
[ordered]@{
    releaseId = $releaseId
    createdAt = (Get-Date).ToString('o')
    sourceCommit = $sourceCommit
    projectRoot = $projectRoot
    scannerConfigSource = $ScannerConfig
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseRoot 'release.json') -Encoding UTF8

Write-Host '[4/6] Checking phone scanner without screen input...' -ForegroundColor Cyan
$checkArgs = @((Join-Path $scannerRelease 'scanner.py'), '--config', $releaseConfigPath, '--check', '--output', (Join-Path $runtimeDir 'scanner-preflight-production'))
if ($Device) { $checkArgs += @('--device', $Device) }
& $pythonPath @checkArgs
if ($LASTEXITCODE -ne 0) { throw 'Scanner preflight failed; no services were started.' }

$backendOut = Join-Path $logDir 'backend.stdout.log'
$backendErr = Join-Path $logDir 'backend.stderr.log'
$frontendOut = Join-Path $logDir 'frontend.stdout.log'
$frontendErr = Join-Path $logDir 'frontend.stderr.log'
$cloudflareOut = Join-Path $logDir 'cloudflared.stdout.log'
$cloudflareErr = Join-Path $logDir 'cloudflared.stderr.log'

Write-Host '[5/6] Starting published services and frozen scanner...' -ForegroundColor Cyan
$savedEnvironment = @{
    DatabasePath = $env:Database__Path
    ScannerConfig = $env:PhoneControl__ScannerConfigPath
    AspNetEnvironment = $env:ASPNETCORE_ENVIRONMENT
    LiveExecution = $env:AutoPlay__LiveExecutionEnabled
    LiveTestMode = $env:AutoPlay__LiveTestModeEnabled
    LiveTestMaxRounds = $env:AutoPlay__LiveTestMaxRounds
}
try {
    $env:Database__Path = $databasePath
    $env:PhoneControl__ScannerConfigPath = $releaseConfigPath
    $env:ASPNETCORE_ENVIRONMENT = 'Production'
    if ($LiveTestMode) {
        $env:AutoPlay__LiveExecutionEnabled = 'true'
        $env:AutoPlay__LiveTestModeEnabled = 'true'
        $env:AutoPlay__LiveTestMaxRounds = '3'
    }
    $backendExe = Join-Path $backendRelease 'GreedyStats.Api.exe'
    $backendArguments = @('--urls', 'http://127.0.0.1:5117')
    if ($LiveTestMode) {
        # Keep the real-device safety mode explicit in the child command line.
        # This is more reliable and auditable than relying only on inherited
        # environment variables when the launcher restores its environment.
        $backendArguments += @(
            '--AutoPlay:LiveExecutionEnabled=true',
            '--AutoPlay:LiveTestModeEnabled=true',
            '--AutoPlay:LiveTestMaxRounds=3'
        )
    }
    $backend = Start-Process -FilePath $backendExe `
        -ArgumentList $backendArguments `
        -WorkingDirectory $backendRelease `
        -WindowStyle Hidden `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -PassThru
} finally {
    $env:Database__Path = $savedEnvironment.DatabasePath
    $env:PhoneControl__ScannerConfigPath = $savedEnvironment.ScannerConfig
    $env:ASPNETCORE_ENVIRONMENT = $savedEnvironment.AspNetEnvironment
    $env:AutoPlay__LiveExecutionEnabled = $savedEnvironment.LiveExecution
    $env:AutoPlay__LiveTestModeEnabled = $savedEnvironment.LiveTestMode
    $env:AutoPlay__LiveTestMaxRounds = $savedEnvironment.LiveTestMaxRounds
}

$savedStaticRoot = $env:STATIC_ROOT
$savedBackendUrl = $env:BACKEND_URL
$savedFrontendPort = $env:FRONTEND_PORT
try {
    $env:STATIC_ROOT = $frontendRelease
    $env:BACKEND_URL = 'http://127.0.0.1:5117'
    $env:FRONTEND_PORT = '5173'
    $frontend = Start-Process -FilePath $nodePath `
        -ArgumentList @((Join-Path $releaseScripts 'production-web-server.mjs')) `
        -WorkingDirectory $releaseRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -PassThru
} finally {
    $env:STATIC_ROOT = $savedStaticRoot
    $env:BACKEND_URL = $savedBackendUrl
    $env:FRONTEND_PORT = $savedFrontendPort
}

$scannerArguments = '"{0}" --config "{1}"' -f (Join-Path $scannerRelease 'scanner.py'), $releaseConfigPath
if ($Device) { $scannerArguments += ' --device "{0}"' -f $Device }
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $pythonPath -Argument $scannerArguments -WorkingDirectory $scannerRelease
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Hidden
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger `
    -Settings $taskSettings -Principal $taskPrincipal `
    -Description "ToolAutoXalat production scanner snapshot $releaseId" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$readyDeadline = (Get-Date).AddSeconds(45)
$backendReady = $false
$frontendReady = $false
$scannerProcess = $null
do {
    try { $backendReady = (Invoke-RestMethod -Uri 'http://127.0.0.1:5117/health' -TimeoutSec 2).status -eq 'ok' } catch { $backendReady = $false }
    try { $frontendReady = (Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $frontendReady = $false }
    $scannerProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($releaseConfigPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        Select-Object -First 1
    if (-not ($backendReady -and $frontendReady -and $scannerProcess)) { Start-Sleep -Milliseconds 500 }
} while ((Get-Date) -lt $readyDeadline -and -not ($backendReady -and $frontendReady -and $scannerProcess))
if (-not ($backendReady -and $frontendReady -and $scannerProcess)) {
    Stop-Process -Id $backend.Id, $frontend.Id -Force -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    throw "Published services did not become ready. See $logDir"
}

Write-Host '[6/6] Starting Cloudflare Quick Tunnel...' -ForegroundColor Cyan
$cloudflared = Start-Process -FilePath $cloudflaredPath `
    -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:5173', '--protocol', 'http2', '--no-autoupdate') `
    -WorkingDirectory $releaseRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $cloudflareOut `
    -RedirectStandardError $cloudflareErr `
    -PassThru

$tunnelDeadline = (Get-Date).AddSeconds(45)
$publicUrl = $null
do {
    Start-Sleep -Milliseconds 500
    if (Test-Path -LiteralPath $cloudflareErr) {
        $logText = Get-Content -LiteralPath $cloudflareErr -Raw -ErrorAction SilentlyContinue
        if ($logText) {
            $match = [regex]::Match($logText, 'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($match.Success) { $publicUrl = $match.Value }
        }
    }
    if ($cloudflared.HasExited) { throw "Cloudflare Tunnel stopped. See $cloudflareErr" }
} while (-not $publicUrl -and (Get-Date) -lt $tunnelDeadline)
if (-not $publicUrl) { throw "Cloudflare URL was not returned in time. See $cloudflareErr" }

$state = [ordered]@{
    startedAt = (Get-Date).ToString('o')
    startMode = 'production-snapshot'
    liveTestMode = [bool]$LiveTestMode
    releaseId = $releaseId
    releasePath = $releaseRoot
    backendPid = $backend.Id
    frontendPid = $frontend.Id
    scannerPid = [int]$scannerProcess.ProcessId
    scannerTaskName = $taskName
    scannerConfigPath = $releaseConfigPath
    scannerSourceSerial = if ($Device) { $Device } else { $releaseConfig.source.serial }
    cloudflaredPid = $cloudflared.Id
    tunnelProvider = 'cloudflare'
    publicUrl = $publicUrl
    cloudflareUrl = $publicUrl
    cloudflareLog = $cloudflareErr
    backendUrl = 'http://127.0.0.1:5117'
    frontendUrl = 'http://127.0.0.1:5173'
    logPath = $logDir
}
$state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath -Encoding UTF8
Set-Content -LiteralPath (Join-Path $runtimeDir 'PUBLIC_URL.txt') -Value $publicUrl -Encoding UTF8
try { Set-Clipboard -Value $publicUrl } catch {}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ' TOOL AUTO XA LAT - PRODUCTION SNAPSHOT DANG CHAY' -ForegroundColor Green
Write-Host " Release   : $releaseId"
Write-Host " Cloudflare: $publicUrl" -ForegroundColor Cyan
Write-Host " Local     : http://127.0.0.1:5173"
Write-Host " Logs      : $logDir"
Write-Host ' URL da duoc copy vao clipboard va runtime\PUBLIC_URL.txt.'
Write-Host ' Source trong IDE co the tiep tuc sua ma khong anh huong ban nay.'
Write-Host '============================================================' -ForegroundColor Green
if ($OpenBrowser) { Start-Process $publicUrl }
