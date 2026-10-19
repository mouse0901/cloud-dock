$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$version=(Get-Content -LiteralPath (Join-Path $root 'version.json') -Raw|ConvertFrom-Json).version
$out=Join-Path 'H:\CodexOutputs\packages\cloud-dock' $version
$compiler='H:\CodexOutputs\tools\inno-6.7.3\compiler\ISCC.exe'
$python=Join-Path $root 'runtime\python\python.exe'
$stage=Join-Path $out ('CloudDock-'+$version+'-Windows-x64')
if(Test-Path -LiteralPath $stage){throw '发行目录已存在。请先归档已有版本，不要覆盖正在分发的包。'}
$bootstrap=Join-Path $root 'work\desktop-build\MicrosoftEdgeWebview2Setup.exe'
$signature=Get-AuthenticodeSignature -LiteralPath $bootstrap
if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft Corporation*'){throw 'WebView2 引导安装器签名无效'}
& (Join-Path $PSScriptRoot 'build_desktop_host.ps1')
Copy-Item -LiteralPath (Join-Path $root ('work\desktop-build\CloudDock'+$version.Replace('.','')+'.exe')) -Destination (Join-Path $root 'CloudDock.exe')
$csc=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:exe /platform:x64 ('/out:'+(Join-Path $root 'work\desktop-build\ReleaseHelper.exe')) (Join-Path $root 'tools\ReleaseHelper.cs') (Join-Path $root 'desktop\host\RuntimePaths.cs')
if($LASTEXITCODE -ne 0){throw 'Release helper compilation failed'}
& $csc /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Core.dll /reference:System.Web.Extensions.dll ('/out:'+(Join-Path $root 'work\desktop-build\UpdateLauncher.exe')) (Join-Path $root 'tools\UpdateLauncher.cs') (Join-Path $root 'desktop\host\UpdateLaunchPolicy.cs') (Join-Path $root 'desktop\host\UpdateRecovery.cs')
if($LASTEXITCODE -ne 0){throw 'Update launcher compilation failed'}
Copy-Item -LiteralPath (Join-Path $root 'work\desktop-build\UpdateLauncher.exe') -Destination (Join-Path $root 'UpdateLauncher.exe') -Force
$env:PYTHONUTF8='1'
& $python -B (Join-Path $PSScriptRoot 'build_desktop_portable.py')
if($LASTEXITCODE -ne 0){throw 'Portable stage failed'}
& $python -B (Join-Path $PSScriptRoot 'finalize_desktop_portable.py')
if($LASTEXITCODE -ne 0){throw 'Archive verification failed'}
& $compiler ('/DAppVersion='+$version) ('/DStage='+$stage) ('/DOutputDir='+$out) (Join-Path $root 'installer\CloudDock.iss')
if($LASTEXITCODE -ne 0){throw 'Installer build failed'}
$installer=Join-Path $out ('CloudDock-'+$version+'-Setup-x64.exe')
$archive=Join-Path $out ('CloudDock-'+$version+'-Windows-x64.zip')
$lines=@()
foreach($file in @($installer,$archive)){$lines+=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+(Split-Path -Leaf $file)}
[System.IO.File]::WriteAllText((Join-Path $out 'CHECKSUMS.sha256'),($lines -join "`n")+"`n",[System.Text.Encoding]::ASCII)
