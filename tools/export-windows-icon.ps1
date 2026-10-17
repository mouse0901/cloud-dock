param([string]$ProjectRoot = 'H:\CodexOutputs\projects\cloud-dock')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$assetDirectory = Join-Path $ProjectRoot 'assets\branding'
$sourcePath = Join-Path $assetDirectory 'cloud-dock.png'
$icoPath = Join-Path $assetDirectory 'cloud-dock.ico'
$sourceImage = [System.Drawing.Image]::FromFile($sourcePath)
$frames = @()
try {
    foreach ($iconSize in @(16,20,24,32,40,48,64,128,256)) {
        $bitmap = [System.Drawing.Bitmap]::new($iconSize, $iconSize, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $stream = [System.IO.MemoryStream]::new()
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage($sourceImage, [System.Drawing.Rectangle]::new(0,0,$iconSize,$iconSize))
            $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
            $frames += [PSCustomObject]@{ Size=$iconSize; Bytes=$stream.ToArray() }
        } finally { $stream.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
    }
} finally { $sourceImage.Dispose() }
$outputStream = [System.IO.File]::Create($icoPath)
$writer = [System.IO.BinaryWriter]::new($outputStream)
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$frames.Count)
    $offset = 6 + 16 * $frames.Count
    foreach ($frame in $frames) {
        $storedSize = if ($frame.Size -eq 256) { 0 } else { $frame.Size }
        $writer.Write([byte]$storedSize); $writer.Write([byte]$storedSize)
        $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$frame.Bytes.Length); $writer.Write([uint32]$offset)
        $offset += $frame.Bytes.Length
    }
    foreach ($frame in $frames) { $writer.Write([byte[]]$frame.Bytes) }
} finally { $writer.Dispose(); $outputStream.Dispose() }
$validationStream = [System.IO.File]::OpenRead($icoPath)
try {
    $icon = [System.Drawing.Icon]::new($validationStream,256,256)
    # GDI+ can choose a smaller embedded frame; validate every PNG payload below.
    try { if ($icon.Width -le 0 -or $icon.Height -le 0) { throw 'Icon failed to decode' } }
    finally { $icon.Dispose() }
} finally { $validationStream.Dispose() }
Write-Output ('ICO generated and decoded: ' + $frames.Count + ' sizes, 16-256 px')
foreach ($frame in $frames) {
    $frameStream = [System.IO.MemoryStream]::new([byte[]]$frame.Bytes)
    try {
        $frameImage = [System.Drawing.Image]::FromStream($frameStream)
        try { if ($frameImage.Width -ne $frame.Size -or $frameImage.Height -ne $frame.Size) { throw 'Embedded PNG dimensions mismatch' } }
        finally { $frameImage.Dispose() }
    } finally { $frameStream.Dispose() }
}
Write-Output 'All nine embedded PNG frames decoded at their declared sizes.'
