param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$backendDir = Join-Path $projectRoot 'apps\backend\GreedyStats.Api'
$runtimeDir = Join-Path $projectRoot 'runtime'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'processes.json'

$listenerLine = netstat -ano -p tcp |
    Where-Object { $_ -match '^\s*TCP\s+127\.0\.0\.1:5117\s+\S+\s+LISTENING\s+\d+\s*$' } |
    Select-Object -First 1

if ($listenerLine) {
    if ($listenerLine -notmatch '(\d+)\s*$') { throw 'Cannot resolve backend listener PID.' }
    $listenerPid = [int]$Matches[1]
    $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
    $expectedBackendPath = Join-Path $backendDir 'bin\Release\net8.0\GreedyStats.Api.exe'
    if (-not $listener -or -not $listener.Path -or
        -not [string]::Equals(
            [System.IO.Path]::GetFullPath($listener.Path),
            [System.IO.Path]::GetFullPath($expectedBackendPath),
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Port 5117 is owned by an unrelated process (PID $listenerPid)."
    }

    Stop-Process -Id $listenerPid -Force
    if (Test-Path -LiteralPath $statePath) {
        $previousState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $parent = Get-Process -Id $previousState.backendPid -ErrorAction SilentlyContinue
        if ($parent -and $parent.ProcessName -eq 'dotnet') {
            Stop-Process -Id $parent.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not $SkipBuild) {
    & dotnet build (Join-Path $backendDir 'GreedyStats.Api.csproj') -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Backend build failed.' }
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null
$backend = Start-Process -FilePath 'dotnet' `
    -ArgumentList @('run', '-c', 'Release', '--no-build', '--urls', 'http://127.0.0.1:5117') `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'backend.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'backend.stderr.log') `
    -PassThru

$deadline = (Get-Date).AddSeconds(30)
$ready = $false
do {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:5117/health' -TimeoutSec 2
        $ready = $health.status -eq 'ok'
    } catch { $ready = $false }
    if (-not $ready) { Start-Sleep -Milliseconds 500 }
} while ((Get-Date) -lt $deadline -and -not $ready)

if (-not $ready) {
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    throw "Backend did not become ready. See $logDir."
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
} else {
    $state = [pscustomobject]@{}
}
$state | Add-Member -NotePropertyName backendPid -NotePropertyValue $backend.Id -Force
$state | Add-Member -NotePropertyName backendUrl -NotePropertyValue 'http://127.0.0.1:5117' -Force
$state | Add-Member -NotePropertyName backendRestartedAt -NotePropertyValue (Get-Date).ToString('o') -Force
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Backend ready: http://127.0.0.1:5117 (PID $($backend.Id))"
