$ErrorActionPreference = 'SilentlyContinue'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$statePath = Join-Path $projectRoot 'runtime\processes.json'

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $rows = @()
    foreach ($entry in @(
        @{ Name = 'Backend'; Pid = $state.backendPid },
        @{ Name = 'Frontend'; Pid = $state.frontendPid },
        @{ Name = 'Scanner'; Pid = $state.scannerPid }
    )) {
        $process = Get-Process -Id $entry.Pid
        $rows += [pscustomobject]@{
            Service = $entry.Name
            Pid = $entry.Pid
            Running = [bool]$process
        }
    }
    $rows | Format-Table -AutoSize
} else {
    Write-Host 'Chưa có runtime\processes.json.'
}

try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:5117/health' -TimeoutSec 3 | ConvertTo-Json -Depth 6
} catch {
    Write-Host 'Backend chưa phản hồi tại http://127.0.0.1:5117.'
}
