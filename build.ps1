$ErrorActionPreference = "Stop"

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffmpegDir = Split-Path $ffmpeg

py -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --windowed `
    --name flac-converter `
    --add-binary "$ffmpeg;." `
    --collect-all rich `
    .\app.py

py -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --console `
    --name flac-converter-cli `
    --add-binary "$ffmpeg;." `
    --collect-all rich `
    .\convert.py

Write-Host "Created dist\flac-converter.exe"
Write-Host "Created dist\flac-converter-cli.exe"