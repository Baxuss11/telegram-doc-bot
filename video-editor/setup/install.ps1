# Установка всего для автомонтажа на Windows: ffmpeg, Python + faster-whisper, Node.js.
# Запуск (PowerShell):  powershell -ExecutionPolicy Bypass -File video-editor\setup\install.ps1
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function WingetInstall($id) {
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

if (-not (Have winget)) {
    Write-Host "Нет winget. Обнови «Установщик приложений» (App Installer) в Microsoft Store и запусти снова." -ForegroundColor Red
    exit 1
}

if (-not (Have ffmpeg)) { Write-Host "-> ставлю ffmpeg";  WingetInstall "Gyan.FFmpeg" }
if (-not (Have python) -or ((python --version 2>&1) -match "Microsoft Store")) {
    Write-Host "-> ставлю Python"; WingetInstall "Python.Python.3.12"
}
if (-not (Have node))   { Write-Host "-> ставлю Node.js"; WingetInstall "OpenJS.NodeJS.LTS" }

python -c "import faster_whisper, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "-> ставлю faster-whisper"
    python -m pip install -q -r "$Here\requirements.txt"
}

Write-Host "`nПроверка:"
(ffmpeg -version)[0]
python --version
python -c "import faster_whisper; print('faster-whisper', faster_whisper.__version__)"
"node " + (node --version)
Write-Host "Готово. Если какая-то команда не находится — закрой и снова открой терминал / Claude." -ForegroundColor Green
