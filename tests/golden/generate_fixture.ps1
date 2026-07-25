param(
    [string]$Ffmpeg = "tools\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe",
    [string]$Output = "tests\golden\golden_synthetic.mp4"
)

$ErrorActionPreference = "Stop"
$ffmpegPath = (Resolve-Path -LiteralPath $Ffmpeg).Path
$outputPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Output))
$outputDirectory = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$filter = @(
    "drawgrid=w=40:h=40:t=1:c=0x29332f",
    "drawbox=x='20+2*t':y=115:w=54:h=24:color=red@0.9:t=fill:enable='between(t,5,15)'",
    "drawbox=x=92:y=62:w=22:h=78:color=red@0.9:t=fill:enable='between(t,30,36)'",
    "drawbox=x=122:y=118:w=26:h=22:color=black@1:t=fill:enable='between(t,33,36)'",
    "drawbox=x=198:y=112:w=30:h=26:color=blue@0.95:t=fill:enable='between(t,45,50)'",
    "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.88:t=fill:enable='between(t,60,65)'",
    "drawbox=x=65:y=100:w=18:h=38:color=white@0.9:t=fill:enable='between(t,85,95)'",
    "drawbox=x=145:y=100:w=18:h=38:color=white@0.9:t=fill:enable='between(t,85,95)'",
    "drawbox=x=225:y=100:w=18:h=38:color=white@0.9:t=fill:enable='between(t,85,95)'"
) -join ","

& $ffmpegPath `
    -hide_banner -nostdin -y `
    -f lavfi -i "color=c=0x101713:s=320x180:r=2:d=120" `
    -vf $filter `
    -an -c:v libx264 -preset medium -crf 28 -pix_fmt yuv420p `
    -movflags +faststart `
    $outputPath

if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg fixture generation failed with exit code $LASTEXITCODE"
}
