$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$run=Join-Path $root ('work\qa-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$fixture=Join-Path $root 'desktop\web\qa-preview.mp4'
Copy-Item -LiteralPath (Join-Path $root 'tests\fixtures\qa-preview.mp4') -Destination $fixture
try {
    $env:CLOUD_DOCK_DATA_DIR=Join-Path $run 'profile'
    $env:CLOUD_DOCK_QA_LIFECYCLE=Join-Path $run 'lifecycle.json'
    $env:CLOUD_DOCK_QA_CAPTURE=Join-Path $run 'capture'
    $process=Start-Process -FilePath (Join-Path $root 'CloudDock.exe') -WorkingDirectory $root -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(45000)) { throw '测试窗口仍在运行，请查看指定测试进程，不要关闭实际使用的云屿。' }
    foreach($file in @($env:CLOUD_DOCK_QA_LIFECYCLE,($env:CLOUD_DOCK_QA_LIFECYCLE+'.productivity.json'))) {
        $result=Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
        $bad=@($result.PSObject.Properties | Where-Object { $_.Value -is [bool] -and -not $_.Value })
        if($bad.Count -gt 0 -or $result.error -or $result.timeout) { throw ('测试失败：'+($bad.Name -join ', ')) }
    }
    Write-Output $run
} finally {
    Remove-Item -LiteralPath $fixture -ErrorAction SilentlyContinue
    foreach($name in @('CLOUD_DOCK_DATA_DIR','CLOUD_DOCK_QA_LIFECYCLE','CLOUD_DOCK_QA_CAPTURE')) { [Environment]::SetEnvironmentVariable($name,$null,'Process') }
}
