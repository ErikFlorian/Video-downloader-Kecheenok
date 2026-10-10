@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.13 -m venv .venv
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -c "import PySide6, yt_dlp, yt_dlp_ejs" >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)
.venv\Scripts\python.exe scripts\prepare_tools.py
if errorlevel 1 goto fail
start "Kecheenok v3" .venv\Scripts\pythonw.exe KecheenokV3.py
exit /b 0
:fail
echo Startup failed. Use 64-bit Python 3.13 and check your internet connection.
pause
exit /b 1
