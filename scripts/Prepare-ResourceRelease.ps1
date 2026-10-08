param(
    [string] $Tag,
    [string] $RepositoryRoot = (Split-Path $PSScriptRoot -Parent),
    [string] $SourceCommit,
    [switch] $CreateTag
)
$ErrorActionPreference = 'Stop'
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
$config = Get-Content -LiteralPath (Join-Path $RepositoryRoot 'current-resource-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (!$Tag) { $Tag = $config.tag }
if ($Tag -cnotmatch '^v\d+\.\d+\.\d+$' -or $Tag -cne $config.tag) { throw 'Tag must match current-resource-info.json.' }
$paths = @('Resources', 'current-resource-info.json')
$untracked = @(git -C $RepositoryRoot ls-files --others --exclude-standard -- $paths)
if ($LASTEXITCODE -ne 0 -or $untracked.Count) { throw 'Commit all resource files before publishing.' }
git -C $RepositoryRoot diff --quiet HEAD -- $paths
if ($LASTEXITCODE -ne 0) { throw 'Commit all resource changes before publishing.' }
$ref = 'refs/tags/' + $Tag
$remote = @(git -C $RepositoryRoot ls-remote --exit-code --tags origin $ref)
$remoteExit = $LASTEXITCODE
if ($remoteExit -eq 0) { throw 'Duplicate resource tag on remote. Choose a new version; existing tags are never overwritten.' }
if ($remoteExit -ne 2) { throw 'Unable to query remote tags. Publication stopped.' }
git -C $RepositoryRoot show-ref --verify --quiet $ref
if ($LASTEXITCODE -eq 0) { throw 'Duplicate local resource tag. Choose a new version.' }
if ($LASTEXITCODE -ne 1) { throw 'Unable to query local tags.' }
$commit = git -C $RepositoryRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve source commit.' }
if ($SourceCommit -and ([string]$commit).Trim() -cne $SourceCommit) { throw 'Source commit changed after the build. Publication stopped.' }
if ($CreateTag) {
    git -C $RepositoryRoot tag $Tag $commit
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create resource tag.' }
    git -C $RepositoryRoot push origin ($ref + ':' + $ref)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to push tag; do not overwrite or reuse it.' }
}
Write-Output ([string]$commit).Trim()
