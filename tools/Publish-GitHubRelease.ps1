param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repository,
    [ValidateSet('stable','beta')][string]$Channel='stable'
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$version=(Get-Content -LiteralPath (Join-Path $root 'version.json') -Raw|ConvertFrom-Json).version
$release=Join-Path 'H:\CodexOutputs\packages\cloud-dock' $version
$manifestResult=& (Join-Path $PSScriptRoot 'New-UpdateManifest.ps1') -Repository $Repository -Channel $Channel
if(-not $?){throw '更新清单生成失败。'}
$manifestResult
$installer=Join-Path $release ('CloudDock-'+$version+'-Setup-x64.exe')
$archive=Join-Path $release ('CloudDock-'+$version+'-Windows-x64.zip')
$checksums=Join-Path $release 'CHECKSUMS.sha256'
$notes=Join-Path $root 'installer\release-notes.txt'
& gh auth status
if($LASTEXITCODE -ne 0){throw '请先完成 GitHub CLI 登录。'}
$flags=@('release','create',('v'+$version),$installer,$archive,$checksums,'--repo',$Repository,'--title',('云屿 '+$version),'--notes-file',$notes,'--draft')
if($Channel -eq 'beta'){$flags+='--prerelease'}
& gh @flags
if($LASTEXITCODE -ne 0){throw 'GitHub 草稿 Release 创建失败。'}
Write-Output '草稿 Release 已创建。请先验收附件，再发布并更新 channels 目录。'
