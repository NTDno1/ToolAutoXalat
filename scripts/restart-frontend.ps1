param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$frontendDir = Join-Path $projectRoot 'apps\frontend'
$runtimeDir = Join-Path $projectRoot 'runtime'
$logDir = Join-Path $runtimeDir 'logs'
$statePath = Join-Path $runtimeDir 'processes.json'

$listener = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if (-not $process -or ($process.Name -notin @('node.exe', 'npm.exe', 'cmd.exe'))) {
        throw "Port 5173 is owned by an unrelated process (PID $($listener.OwningProcess))."
    }
    Stop-Process -Id $listener.OwningProcess -Force
}

if (-not $SkipBuild) {
    & npm.cmd run build --prefix $frontendDir
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null
$frontend = Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'preview', '--', '--host', '127.0.0.1', '--port', '5173') `
    -WorkingDirectory $frontendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'frontend.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'frontend.stderr.log') `
    -PassThru

$deadline = (Get-Date).AddSeconds(30)
$ready = $false
do {
    try {
        $ready = (Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
    } catch { $ready = $false }
    if (-not $ready) { Start-Sleep -Milliseconds 500 }
} while ((Get-Date) -lt $deadline -and -not $ready)

if (-not $ready) {
    Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
    throw "Frontend did not become ready. See $logDir."
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
} else {
    $state = [pscustomobject]@{}
}
$state | Add-Member -NotePropertyName frontendPid -NotePropertyValue $frontend.Id -Force
$state | Add-Member -NotePropertyName frontendUrl -NotePropertyValue 'http://127.0.0.1:5173' -Force
$state | Add-Member -NotePropertyName frontendMode -NotePropertyValue 'built-preview' -Force
$state | Add-Member -NotePropertyName frontendRestartedAt -NotePropertyValue (Get-Date).ToString('o') -Force
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Frontend ready: http://127.0.0.1:5173 (PID $($frontend.Id), built preview)"
