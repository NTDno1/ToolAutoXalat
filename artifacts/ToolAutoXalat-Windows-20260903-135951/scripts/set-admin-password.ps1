param(
    [Parameter(Mandatory = $true)]
    [string]$Password,
    [string]$Username = 'admin',
    [ValidateRange(1, 24)]
    [int]$SessionHours = 8
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $projectRoot 'apps\backend\GreedyStats.Api\appsettings.Admin.json'
$iterations = 210000
$salt = [byte[]]::new(16)
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$random.GetBytes($salt)
$random.Dispose()
$derive = [Security.Cryptography.Rfc2898DeriveBytes]::new(
    $Password,
    $salt,
    $iterations,
    [Security.Cryptography.HashAlgorithmName]::SHA256)
$hash = $derive.GetBytes(32)
$derive.Dispose()
$encoded = 'PBKDF2-SHA256${0}${1}${2}' -f `
    $iterations,
    [Convert]::ToBase64String($salt),
    [Convert]::ToBase64String($hash)

$config = [ordered]@{
    Admin = [ordered]@{
        Username = $Username
        PasswordHash = $encoded
        SessionHours = $SessionHours
    }
}
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding utf8
Write-Host "Admin credentials updated for '$Username'. Restart backend to apply immediately."
