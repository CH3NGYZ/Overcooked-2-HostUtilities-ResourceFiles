param(
    [string] $RepositoryRoot = (Split-Path $PSScriptRoot -Parent),
    [string] $OutputDirectory,
    [string] $SourceCommit
)
$ErrorActionPreference = 'Stop'
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
if (!$OutputDirectory) { $OutputDirectory = Join-Path $RepositoryRoot 'artifacts\resources' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if ((Test-Path -LiteralPath $OutputDirectory) -and @(Get-ChildItem -LiteralPath $OutputDirectory -Force).Count) {
    throw 'Use a fresh, empty output directory.'
}
if (!$SourceCommit) {
    $SourceCommit = git -C $RepositoryRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve source commit.' }
}
if ($SourceCommit -notmatch '^[a-fA-F0-9]{40}$') { throw 'Expected a full Git commit SHA.' }
$release = Get-Content -LiteralPath (Join-Path $RepositoryRoot 'current-resource-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($release.tag -cnotmatch '^v(\d+\.\d+\.\d+)$' -or
    $release.minModVersion -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$' -or
    $release.PSObject.Properties['versionCode'] -or $release.PSObject.Properties['version'] -or
    ($release.resourceFormat -isnot [int] -and $release.resourceFormat -isnot [long]) -or
    $release.resourceFormat -lt 1 -or $release.changelog -isnot [string]) { throw 'Invalid current-resource-info.json.' }
$localizedLogs = $null
if ($release.PSObject.Properties['changelogs']) {
    if ($release.changelogs -isnot [pscustomobject] -or !$release.changelogs.PSObject.Properties.Count) { throw 'Invalid localized resource changelogs.' }
    $localizedLogs = [ordered]@{}
    foreach ($entry in $release.changelogs.PSObject.Properties) {
        if ($entry.Name -cnotin @('zh-cn','en-us','ko-kr') -or $entry.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($entry.Value)) {
            throw 'Invalid resource changelog language or text.'
        }
        $localizedLogs[$entry.Name] = $entry.Value
    }
}
$tag = $release.tag
$release | Add-Member -NotePropertyName version -NotePropertyValue ($tag.Substring(1))
$resourceFolder = 'OC2HostUtilitiesResource'
$manifestName = 'HostUtilities.Resources.manifest'
$infoName = 'HostUtilities.Resources.info.json'
$utf8 = New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.IO.Compression

# Archive paths are relative to Resources, with no enclosing ZIP directory.
$resourceRoot = Join-Path $RepositoryRoot 'Resources'
if (!(Test-Path -LiteralPath $resourceRoot -PathType Container)) { throw 'Missing Resources directory.' }
$resourcePrefix = $resourceRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if ($OutputDirectory.Equals($resourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $OutputDirectory.StartsWith($resourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output directory must be outside Resources.'
}
$files = @{}
$directoryNames = @()
foreach ($item in Get-ChildItem -LiteralPath $resourceRoot -Recurse -Force) {
    $relative = $item.FullName.Substring($resourcePrefix.Length).Replace('\', '/')
    if ($relative -match '[\r\n]') { throw "Unsupported ZIP entry: $relative" }
    if ($item.PSIsContainer) {
        $directoryNames += $relative + '/'
        continue
    }
    if ($relative -ieq $manifestName -or $relative -ieq $infoName) {
        throw "Reserved generated resource file: $relative"
    }
    if ($item.Length -ge [uint32]::MaxValue) { throw "Unsupported ZIP entry: $relative" }
    if ($files.ContainsKey($relative)) { throw "Duplicate resource path: $relative" }
    $files[$relative] = $item.FullName
}
if (!$files.Count -or ($files.Count + $directoryNames.Count) -gt 65532) {
    throw 'Empty resource package or too many entries for the MOD ZIP reader.'
}
$size = ($files.Values | ForEach-Object { (Get-Item -LiteralPath $_).Length } | Measure-Object -Sum).Sum
if ($size -ge 3GB) { throw 'Resource package exceeds the supported ZIP size.' }
[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
$info = [ordered]@{
    schemaVersion = 1
    resourceId = $resourceFolder
    version = $release.version
    minModVersion = $release.minModVersion
    resourceFormat = $release.resourceFormat
    sourceCommit = $SourceCommit.ToLowerInvariant()
}
$infoPath = Join-Path $OutputDirectory $infoName
[IO.File]::WriteAllText($infoPath, (($info | ConvertTo-Json -Depth 6) + [Environment]::NewLine), $utf8)
$files[$infoName] = $infoPath
$relativeNames = [string[]]@($files.Keys)
[Array]::Sort($relativeNames, [StringComparer]::Ordinal)
$manifest = foreach ($relative in $relativeNames) {
    $file = Get-Item -LiteralPath $files[$relative]
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    '{0} {1} {2}' -f $hash, $file.Length, $relative
}
$manifestPath = Join-Path $OutputDirectory $manifestName
[IO.File]::WriteAllText($manifestPath, (($manifest -join [char]10) + [char]10), $utf8)
$files[$manifestName] = $manifestPath

$zipPath = Join-Path $OutputDirectory 'HostUtilities.Resources.zip'
$stream = [IO.File]::Open($zipPath, [IO.FileMode]::CreateNew)
try {
    # Use the built-in UTF8 encoding so .NET Framework sets the ZIP UTF-8 flag.
    $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create, $true, ([Text.Encoding]::UTF8))
    try {
        $entryNames = [string[]](@($files.Keys) + $directoryNames)
        [Array]::Sort($entryNames, [StringComparer]::Ordinal)
        foreach ($relative in $entryNames) {
            $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
            # Fixed timestamp means retries of the same source produce the same package bytes.
            $entry.LastWriteTime = [DateTimeOffset]::new(2020, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            if ($relative.EndsWith('/')) { continue }
            $target = $entry.Open()
            try {
                $source = [IO.File]::OpenRead($files[$relative])
                try { $source.CopyTo($target) } finally { $source.Dispose() }
            } finally { $target.Dispose() }
        }
    } finally { $archive.Dispose() }
} finally { $stream.Dispose() }
$metadata = [ordered]@{
    version = $release.version
    tag = $tag
    sourceCommit = $SourceCommit.ToLowerInvariant()
    minModVersion = $release.minModVersion
    resourceFormat = $release.resourceFormat
    changelog = $release.changelog
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
    manifestSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant()
    size = (Get-Item -LiteralPath $zipPath).Length
}
if ($null -ne $localizedLogs) { $metadata.changelogs = $localizedLogs }
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'release-metadata.json'), (($metadata | ConvertTo-Json -Depth 8) + [Environment]::NewLine), $utf8)
$notes = if ($null -ne $localizedLogs) {
    foreach ($entry in $localizedLogs.GetEnumerator()) { '## ' + $entry.Key; ''; $entry.Value; '' }
} else { $release.changelog }
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'release-notes.md'), (($notes -join [Environment]::NewLine) + [Environment]::NewLine), $utf8)
Write-Output "Tag: $tag"
Write-Output "Files: $($files.Count), ZIP: $zipPath"
