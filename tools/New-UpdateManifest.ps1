param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repository,
    [ValidateSet('stable','beta')][string]$Channel='stable',
    [string]$PrivateKey='H:\CodexOutputs\secrets\cloud-dock\update-signing-private.xml'
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$version=(Get-Content -LiteralPath (Join-Path $root 'version.json') -Raw|ConvertFrom-Json).version
$release=Join-Path 'H:\CodexOutputs\packages\cloud-dock' $version
$installer=Join-Path $release ('CloudDock-'+$version+'-Setup-x64.exe')
if(-not (Test-Path -LiteralPath $installer -PathType Leaf)){throw '请先生成安装包。'}
if(-not (Test-Path -LiteralPath $PrivateKey -PathType Leaf)){throw '更新签名私钥不存在。'}
$digest=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$size=(Get-Item -LiteralPath $installer).Length
$notes=(Get-Content -LiteralPath (Join-Path $root 'installer\release-notes.txt') -Raw).Trim()
$manifest=[ordered]@{
    schema=1
    product='cloud-dock'
    channel=$Channel
    version=$version
    published_at=[DateTime]::UtcNow.ToString('o')
    notes=$notes
    artifact=[ordered]@{
        kind='installer'
        architecture='windows-x64'
        url=('https://github.com/'+$Repository+'/releases/download/v'+$version+'/CloudDock-'+$version+'-Setup-x64.exe')
        size=$size
        sha256=$digest
    }
}
$channelDir=Join-Path $release 'github\channels'
New-Item -ItemType Directory -Force -Path $channelDir|Out-Null
$manifestPath=Join-Path $channelDir ($Channel+'.json')
$signaturePath=Join-Path $channelDir ($Channel+'.sig')
$json=($manifest|ConvertTo-Json -Depth 8).Replace("`r`n","`n").Replace("`r","`n")
$encoding=New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($manifestPath,$json,$encoding)
$rsa=New-Object System.Security.Cryptography.RSACryptoServiceProvider
$rsa.FromXmlString([System.IO.File]::ReadAllText($PrivateKey))
$signature=$rsa.SignData($encoding.GetBytes($json),'SHA256')
[System.IO.File]::WriteAllText($signaturePath,[Convert]::ToBase64String($signature),$encoding)
$rsa.Dispose()
[ordered]@{manifest=$manifestPath;signature=$signaturePath;installer=$installer;sha256=$digest;repository=$Repository;channel=$Channel}|ConvertTo-Json
