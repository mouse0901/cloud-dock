param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repository)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$path=Join-Path $root 'update-source.json'
$source=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
$source.enabled=$true
$source.repository=$Repository
foreach($channel in @('stable','beta')){
    $base='https://raw.githubusercontent.com/'+$Repository+'/main/channels/'+$channel
    $source.channels.$channel.manifest=$base+'.json'
    $source.channels.$channel.signature=$base+'.sig'
}
$json=$source|ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($path,$json,(New-Object System.Text.UTF8Encoding($false)))
Write-Output ('已配置公开更新仓库：'+$Repository)
