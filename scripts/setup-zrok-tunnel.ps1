param(
    [string]$AccountToken = '',

    [ValidatePattern('^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$')]
    [string]$ShareName = 'toolautoxalat-1504'
)

$ErrorActionPreference = 'Stop'
$zrokPath = Join-Path $env:LOCALAPPDATA 'ToolAutoXalat\zrok2\zrok2.exe'
if (-not (Test-Path -LiteralPath $zrokPath)) {
    $zrokPath = (Get-Command zrok2 -ErrorAction Stop).Source
}

if (-not $AccountToken) {
    $secureToken = Read-Host 'Nhap account token cua myzrok.io' -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $AccountToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
}
if (-not $AccountToken) {
    throw 'A myzrok.io account token is required.'
}

$previousPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $overviewOutput = @(& $zrokPath overview --json 2>&1 | ForEach-Object { $_.ToString() })
    $overviewExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousPreference
}

if ($overviewExitCode -ne 0) {
    & $zrokPath enable $AccountToken --headless -d 'ToolAutoXalat'
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot enable the local zrok environment with this account token.'
    }
}

$namesJson = & $zrokPath list names --json -n public
if ($LASTEXITCODE -ne 0) {
    throw 'Cannot read zrok reserved names after enabling the environment.'
}
$names = @($namesJson | ConvertFrom-Json)
$reservedName = $names | Where-Object { $_.namespaceToken -eq 'public' -and $_.name -eq $ShareName } | Select-Object -First 1
if (-not $reservedName) {
    & $zrokPath create name -n public $ShareName
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot reserve 'public:$ShareName'. The name may already belong to another zrok account."
    }
}

# Do not retain the account token in this process after setup completes.
$AccountToken = $null
& (Join-Path $PSScriptRoot 'start-zrok-tunnel.ps1') -ShareName $ShareName
