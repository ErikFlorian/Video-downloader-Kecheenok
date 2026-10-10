@echo off
setlocal
cd /d "%~dp0"
py -3.13 -c "import sys, struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8"
if errorlevel 1 (
    echo 64-bit Python 3.13 for Windows is required. Download it from python.org.
    pause
    exit /b 1
)
py -3.13 -m venv .venv-build
if errorlevel 1 goto fail
.venv-build\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 goto fail
.venv-build\Scripts\python.exe scripts\build_windows.py
if errorlevel 1 goto fail
echo Done. The Windows ZIP is in the dist folder.
pause
exit /b 0
:fail
echo Build failed. See the details above. The package was not marked as complete.
pause
exit /b 1
