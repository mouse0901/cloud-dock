$ErrorActionPreference='Stop'
$version=(Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'version.json') -Raw|ConvertFrom-Json).version
$stage=Join-Path ('H:\CodexOutputs\packages\cloud-dock\'+$version) ('CloudDock-'+$version+'-Windows-x64')
$run=Join-Path 'H:\CodexOutputs\qa\cloud-dock' ($version+'-package-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run|Out-Null
$keys=@('CLOUD_DOCK_DATA_DIR','CLOUD_DOCK_QA_LIFECYCLE','CLOUD_DOCK_QA_CAPTURE','CLOUD_DOCK_QA_TIMINGS')
$prior=@{};foreach($k in $keys){$prior[$k]=[Environment]::GetEnvironmentVariable($k,'Process')}
try{
 $env:CLOUD_DOCK_DATA_DIR=Join-Path $run 'profile'
 $env:CLOUD_DOCK_QA_LIFECYCLE=Join-Path $run 'lifecycle.json'
 $env:CLOUD_DOCK_QA_CAPTURE=Join-Path $run 'capture'
 $env:CLOUD_DOCK_QA_TIMINGS=Join-Path $run 'timings.txt'
 $p=Start-Process -FilePath (Join-Path $stage 'CloudDock.exe') -WorkingDirectory $stage -WindowStyle Hidden -PassThru
 if(-not $p.WaitForExit(60000)){throw ('Package test still running: '+$p.Id)}
 $v=Get-Content -LiteralPath $env:CLOUD_DOCK_QA_LIFECYCLE -Raw|ConvertFrom-Json
 $bad=@($v.PSObject.Properties|Where-Object{$_.Value -is [bool] -and -not $_.Value})
 if($bad.Count -or $v.error -or $v.timeout){throw ('Package failed: '+($bad.Name -join ', '))}
 $count=@($v.PSObject.Properties|Where-Object{$_.Value -is [bool]}).Count
 Write-Output ('Package checks passed: '+$count+'; '+$run)
}finally{foreach($k in $keys){[Environment]::SetEnvironmentVariable($k,$prior[$k],'Process')}}
