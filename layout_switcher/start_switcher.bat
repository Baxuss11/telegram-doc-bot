@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing dependencies...
python -m pip install --quiet -r requirements.txt
python switcher.py
pause
