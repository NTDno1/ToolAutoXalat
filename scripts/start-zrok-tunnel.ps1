param(
    [ValidateRange(1, 65535)]
    [int]$TargetPort = 5173,

    [ValidatePattern('^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$')]
    [string]$ShareName = 'toolautoxalat-1504'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$logDir = Join-Path $projectRoot 'runtime\logs'
$zrokPath = Join-Path $env:LOCALAPPDATA 'ToolAutoXalat\zrok2\zrok2.exe'

if (-not (Test-Path -LiteralPath $zrokPath)) {
    $zrokPath = (Get-Command zrok2 -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$agentOut = Join-Path $logDir 'zrok-agent.stdout.log'
$agentErr = Join-Path $logDir 'zrok-agent.stderr.log'

function Invoke-ZrokCapture {
    param([string[]]$Arguments)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& $zrokPath @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    [pscustomobject]@{
        ExitCode = $exitCode
        Lines = $lines
        Text = ($lines -join [Environment]::NewLine)
    }
}

function Get-ZrokAgentProcess {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -eq $zrokPath -and
            $_.CommandLine -match '(?i)\bagent\s+start\b'
        } |
        Select-Object -First 1
}

$overview = Invoke-ZrokCapture -Arguments @('overview', '--json')
if ($overview.ExitCode -ne 0) {
    throw 'zrok is not connected to an account. Run scripts\setup-zrok-tunnel.ps1 first.'
}

$namesResult = Invoke-ZrokCapture -Arguments @('list', 'names', '--json', '-n', 'public')
if ($namesResult.ExitCode -ne 0) {
    throw "Cannot read zrok reserved names: $($namesResult.Text)"
}
$names = @($namesResult.Text | ConvertFrom-Json)
$reservedName = $names | Where-Object { $_.namespaceToken -eq 'public' -and $_.name -eq $ShareName } | Select-Object -First 1
if (-not $reservedName -or -not $reservedName.reserved) {
    throw "The reserved zrok name 'public:$ShareName' is missing. Run scripts\setup-zrok-tunnel.ps1 first."
}

$publicUrl = "https://$($reservedName.name).$($reservedName.namespaceName)"
$agentCheck = Invoke-ZrokCapture -Arguments @('agent', 'status')
if ($agentCheck.ExitCode -ne 0) {
    foreach ($path in $agentOut, $agentErr) {
        if (Test-Path -LiteralPath $path) {
            Clear-Content -LiteralPath $path -ErrorAction SilentlyContinue
        } else {
            New-Item -ItemType File -Path $path -Force | Out-Null
        }
    }

    Start-Process -FilePath $zrokPath `
        -ArgumentList @('agent', 'start') `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $agentOut `
        -RedirectStandardError $agentErr | Out-Null

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $agentCheck = Invoke-ZrokCapture -Arguments @('agent', 'status')
    } while ($agentCheck.ExitCode -ne 0 -and (Get-Date) -lt $deadline)

    if ($agentCheck.ExitCode -ne 0) {
        throw "zrok agent did not start. See $agentErr"
    }
}

# A reserved share is restored automatically by the agent. Create it only when
# it is not already active so repeated project starts remain idempotent.
$agentCheck = Invoke-ZrokCapture -Arguments @('agent', 'status')
if ($agentCheck.Text -notmatch [regex]::Escape($publicUrl)) {
    $share = Invoke-ZrokCapture -Arguments @(
        'share', 'public', "http://127.0.0.1:$TargetPort",
        '--force-agent', '--open', '-n', "public:$ShareName"
    )
    if ($share.ExitCode -ne 0) {
        throw "Cannot start the zrok public share: $($share.Text)"
    }
}

$deadline = (Get-Date).AddSeconds(30)
do {
    $agentCheck = Invoke-ZrokCapture -Arguments @('agent', 'status')
    if ($agentCheck.ExitCode -eq 0 -and $agentCheck.Text -match [regex]::Escape($publicUrl)) {
        break
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if ($agentCheck.ExitCode -ne 0 -or $agentCheck.Text -notmatch [regex]::Escape($publicUrl)) {
    throw "zrok agent is running but the named share $publicUrl did not become active."
}

$agentProcess = Get-ZrokAgentProcess
[pscustomobject]@{
    Provider = 'zrok'
    PublicUrl = $publicUrl
    ShareName = $ShareName
    AgentPid = if ($agentProcess) { [int]$agentProcess.ProcessId } else { $null }
    TargetUrl = "http://127.0.0.1:$TargetPort"
    AgentLog = $agentErr
}
