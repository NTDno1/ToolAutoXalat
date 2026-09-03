param(
    [int]$KeepLatestErrorCaptures = 100
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'runtime')).Path
$captureRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'data\captures')).Path
$separator = [IO.Path]::DirectorySeparatorChar

if (-not $runtimeRoot.StartsWith($projectRoot + $separator, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Runtime path is outside the workspace: $runtimeRoot"
}
if (-not $captureRoot.StartsWith($projectRoot + $separator, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Capture path is outside the workspace: $captureRoot"
}

$temporaryDirectories = @(
    Get-ChildItem -LiteralPath $runtimeRoot -Directory -Force |
        Where-Object {
            $_.Name -in @('buildcheck', 'build-check', 'build-backend-check') -or
            $_.Name -like 'responsive-cdp-*' -or
            $_.Name -like 'edge-*' -or
            $_.Name -like 'chrome-*'
        }
)

$temporaryBytes = 0
foreach ($directory in $temporaryDirectories) {
    if (-not $directory.FullName.StartsWith($runtimeRoot + $separator, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unsafe runtime path: $($directory.FullName)"
    }
    $temporaryBytes += [long](
        Get-ChildItem -LiteralPath $directory.FullName -File -Recurse -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum
    ).Sum
}

$errorCaptures = @(
    Get-ChildItem -LiteralPath $captureRoot -File -Filter 'error_*.png' |
        Sort-Object -Property LastWriteTime -Descending
)
$oldErrorCaptures = @($errorCaptures | Select-Object -Skip $KeepLatestErrorCaptures)
$errorBytes = [long]($oldErrorCaptures | Measure-Object -Property Length -Sum).Sum

foreach ($file in $oldErrorCaptures) {
    if (
        -not [string]::Equals($file.DirectoryName, $captureRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $file.Name -notlike 'error_*.png'
    ) {
        throw "Refusing to remove an unsafe capture path: $($file.FullName)"
    }
}

foreach ($directory in $temporaryDirectories) {
    Remove-Item -LiteralPath $directory.FullName -Recurse -Force
}
foreach ($file in $oldErrorCaptures) {
    Remove-Item -LiteralPath $file.FullName -Force
}

$driveName = [IO.Path]::GetPathRoot($projectRoot).TrimEnd('\').TrimEnd(':')
$drive = Get-PSDrive -Name $driveName
[pscustomobject]@{
    RemovedTemporaryDirectories = $temporaryDirectories.Count
    RemovedTemporaryMB = [math]::Round($temporaryBytes / 1MB, 1)
    RemovedOldErrorCaptures = $oldErrorCaptures.Count
    RemovedErrorCaptureMB = [math]::Round($errorBytes / 1MB, 1)
    RetainedErrorCaptures = @(
        Get-ChildItem -LiteralPath $captureRoot -File -Filter 'error_*.png'
    ).Count
    FreeGB = [math]::Round($drive.Free / 1GB, 2)
} | ConvertTo-Json
