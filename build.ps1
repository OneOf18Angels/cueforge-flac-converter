$ErrorActionPreference = "Stop"

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffmpegDir = Split-Path $ffmpeg

py -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --console `
    --name flac-converter `
    --add-binary "$ffmpeg;." `
    --collect-all rich `
    .\app.py

Write-Host "Created dist\flac-converter.exe"