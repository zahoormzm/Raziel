[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot,

    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [switch]$AllowReplace
)

$ErrorActionPreference = "Stop"
$source = [System.IO.Path]::GetFullPath($SourceRoot)
$destination = [System.IO.Path]::GetFullPath($DestinationRoot)
$manifestFile = [System.IO.Path]::GetFullPath($ManifestPath)

if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "Source root does not exist: $source"
}
if ($destination -eq [System.IO.Path]::GetPathRoot($destination)) {
    throw "Destination root may not be a filesystem root."
}
if ($source -eq $destination) {
    throw "Source and destination roots must differ."
}

$manifest = Get-Content -Raw -LiteralPath $manifestFile | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$destinationPrefix = $destination.TrimEnd('\') + '\'
$stamp = Get-Date -Format "yyyyMMddTHHmmssfff"

foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    if ([System.IO.Path]::IsPathRooted($relative)) {
        throw "Manifest path must be relative: $relative"
    }
    $sourceFile = [System.IO.Path]::GetFullPath((Join-Path $source $relative))
    $targetFile = [System.IO.Path]::GetFullPath((Join-Path $destination $relative))
    if (-not $sourceFile.StartsWith($source.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest source escapes source root: $relative"
    }
    if (-not $targetFile.StartsWith($destinationPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest destination escapes destination root: $relative"
    }
    if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) {
        throw "Manifest source is missing: $sourceFile"
    }
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceFile).Hash.ToLowerInvariant()
    if ($sourceHash -ne ([string]$entry.sha256).ToLowerInvariant()) {
        throw "Source checksum mismatch: $relative"
    }
    if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetFile).Hash.ToLowerInvariant()
        if ($targetHash -eq $sourceHash) {
            continue
        }
        if (-not $AllowReplace) {
            throw "Replica conflict at $relative. Re-run with -AllowReplace to keep a backup and replace it."
        }
    }
    $targetDirectory = Split-Path -Parent $targetFile
    New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
    $temporary = Join-Path $targetDirectory (".sync-" + [System.IO.Path]::GetFileName($targetFile) + "-" + $stamp)
    Copy-Item -LiteralPath $sourceFile -Destination $temporary
    $temporaryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash.ToLowerInvariant()
    if ($temporaryHash -ne $sourceHash) {
        Remove-Item -LiteralPath $temporary
        throw "Copied checksum mismatch: $relative"
    }
    if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
        $backup = $targetFile + ".previous-" + $stamp
        Move-Item -LiteralPath $targetFile -Destination $backup
    }
    Move-Item -LiteralPath $temporary -Destination $targetFile
}

[pscustomobject]@{
    source = $source
    destination = $destination
    files_verified = @($manifest.files).Count
    manifest_sha256 = $manifest.manifest_sha256
    replacement_backups_retained = [bool]$AllowReplace
} | ConvertTo-Json
