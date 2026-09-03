param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot 'artifacts'
}
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packageName = "ToolAutoXalat-Windows-$stamp"
$packageRoot = Join-Path $outputRoot $packageName
$archivePath = Join-Path $outputRoot "$packageName.zip"
if (Test-Path -LiteralPath $packageRoot) { throw "Package directory already exists: $packageRoot" }
if (Test-Path -LiteralPath $archivePath) { throw "Package archive already exists: $archivePath" }
New-Item -ItemType Directory -Path $packageRoot | Out-Null

$excludedDirectories = @(
    (Join-Path $projectRoot '.git'),
    (Join-Path $projectRoot 'artifacts'),
    (Join-Path $projectRoot 'runtime'),
    (Join-Path $projectRoot 'logs'),
    (Join-Path $projectRoot 'data'),
    (Join-Path $projectRoot 'apps\frontend\node_modules'),
    (Join-Path $projectRoot 'apps\frontend\dist'),
    (Join-Path $projectRoot 'apps\backend\GreedyStats.Api\bin'),
    (Join-Path $projectRoot 'apps\backend\GreedyStats.Api\obj')
)

$robocopyArguments = @(
    $projectRoot,
    $packageRoot,
    '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
    '/XD'
) + $excludedDirectories + @('/XF', '*.pyc')
& robocopy @robocopyArguments | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Source export failed; robocopy exit code $LASTEXITCODE" }

$databaseSource = Join-Path $projectRoot 'data\greedy_stats.db'
$databaseDestination = Join-Path $packageRoot 'data\greedy_stats.db'
$backupOutput = & python (Join-Path $PSScriptRoot 'export_database.py') `
    $databaseSource $databaseDestination
if ($LASTEXITCODE -ne 0) { throw 'SQLite snapshot failed.' }
$backupInfo = $backupOutput | ConvertFrom-Json

$packageInfo = @"
ToolAutoXalat portable Windows package
Created: $((Get-Date).ToString('o'))
SQLite integrity: $($backupInfo.integrity)
Database results: $($backupInfo.results)
Database bytes: $($backupInfo.bytes)

Excluded because they are machine-specific or regenerated:
- .git, runtime process IDs, logs
- node_modules, frontend dist, backend bin/obj
- data/captures and old database backups

Read MIGRATE_WINDOWS.md before starting on the new machine.
This package can contain private admin/API configuration. Keep it private.
"@
$packageInfo | Set-Content -LiteralPath (Join-Path $packageRoot 'PACKAGE_INFO.txt') -Encoding utf8

Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal
$hash = Get-FileHash -LiteralPath $archivePath -Algorithm SHA256

[pscustomobject]@{
    Archive = $archivePath
    SizeMB = [math]::Round((Get-Item -LiteralPath $archivePath).Length / 1MB, 2)
    SHA256 = $hash.Hash
    DatabaseResults = $backupInfo.results
    DatabaseIntegrity = $backupInfo.integrity
} | Format-List
