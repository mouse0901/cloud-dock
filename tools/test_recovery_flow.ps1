$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$run=Join-Path $root ('work\recovery-flow-'+[guid]::NewGuid().ToString('N'))
$app=Join-Path $run 'app'
$data=Join-Path $run 'profile'
$updates=Join-Path $data 'updates'
New-Item -ItemType Directory -Path $app,$updates | Out-Null
$csc=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$program=Join-Path $app 'CloudDock.exe'
& $csc /nologo /target:winexe ('/out:'+$program) (Join-Path $root 'tests\RecoveryFakeApp.cs')
if($LASTEXITCODE -ne 0){throw 'Fixture failed'}
$installer=Join-Path $updates 'CloudDock-0.6.13-Setup-x64.exe'
& $csc /nologo /target:winexe ('/out:'+$installer) (Join-Path $root 'tests\RecoveryFailedInstaller.cs')
if($LASTEXITCODE -ne 0){throw 'Fixture failed'}
$launcher=Join-Path $run 'UpdateLauncher.exe'
& $csc /nologo /target:winexe /reference:System.Core.dll /reference:System.Web.Extensions.dll ('/out:'+$launcher) (Join-Path $root 'tools\UpdateLauncher.cs') (Join-Path $root 'desktop\host\UpdateLaunchPolicy.cs') (Join-Path $root 'desktop\host\UpdateRecovery.cs')
if($LASTEXITCODE -ne 0){throw 'Launcher failed'}
[IO.File]::WriteAllText((Join-Path $app 'version.json'),'{"version":"0.6.12"}')
[IO.File]::WriteAllText((Join-Path $data 'private.txt'),'user-data')
$oldHash=(Get-FileHash -LiteralPath $program -Algorithm SHA256).Hash.ToLowerInvariant()
$versionHash=(Get-FileHash -LiteralPath (Join-Path $app 'version.json') -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllLines((Join-Path $app 'CHECKSUMS.sha256'),@(($oldHash+'  CloudDock.exe'),($versionHash+'  version.json')))
$sha=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$arguments=@($installer,$sha,'0.6.13',$program,$data,(Join-Path $run 'update.log'),'2147483647') | ForEach-Object {'"'+$_+'"'}
$p=Start-Process -FilePath $launcher -ArgumentList $arguments -WorkingDirectory $run -WindowStyle Hidden -PassThru
if(-not $p.WaitForExit(15000)){throw 'Isolated recovery test timed out'}
if($p.ExitCode -ne 6){throw ('Unexpected failure exit '+$p.ExitCode)}
if((Get-FileHash -LiteralPath $program -Algorithm SHA256).Hash.ToLowerInvariant() -ne $oldHash){throw 'Old program not restored'}
if([IO.File]::ReadAllText((Join-Path $data 'private.txt')) -ne 'user-data'){throw 'Data changed'}
$state=Get-Content -LiteralPath (Join-Path $updates 'recovery\state.json') -Raw|ConvertFrom-Json
if($state.status -ne 'restored'){throw 'Recovery status missing'}
$deadline=[DateTime]::UtcNow.AddSeconds(3)
while(-not (Test-Path -LiteralPath (Join-Path $app 'restart-observed.txt')) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
if(-not (Test-Path -LiteralPath (Join-Path $app 'restart-observed.txt'))){throw 'Old app restart not observed'}
Write-Output ('Failed installer recovery passed: 5 checks. '+$run)
