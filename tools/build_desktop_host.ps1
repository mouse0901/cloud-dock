$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$buildRoot = Join-Path $projectRoot 'work\desktop-build'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'version.json') -Raw | ConvertFrom-Json).version
$output = Join-Path $buildRoot ('CloudDock' + $version.Replace('.','') + '.exe')
$refs = @('System.dll','System.Core.dll','System.Drawing.dll','System.Windows.Forms.dll','System.Web.Extensions.dll',
    (Join-Path $projectRoot 'Microsoft.Web.WebView2.Core.dll'),(Join-Path $projectRoot 'Microsoft.Web.WebView2.WinForms.dll'))
$arguments = @('/nologo','/target:winexe','/platform:x64','/optimize+',('/out:' + $output),('/win32icon:' + (Join-Path $projectRoot 'assets\branding\cloud-dock.ico')))
$arguments += $refs | ForEach-Object { '/reference:' + $_ }
$arguments += Get-ChildItem -LiteralPath (Join-Path $projectRoot 'desktop\host') -Filter '*.cs' | ForEach-Object FullName
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw 'Native host compilation failed' }
Write-Output $output
