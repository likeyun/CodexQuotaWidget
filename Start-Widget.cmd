@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\CodexQuota\CodexQuota.exe" (
    start "" "%~dp0dist\CodexQuota\CodexQuota.exe"
    exit /b 0
)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    python -m venv "%~dp0.venv"
    if errorlevel 1 goto :failed
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto :failed
)
"%~dp0.venv\Scripts\python.exe" -c "import PySide6, curl_cffi" >nul 2>&1
if errorlevel 1 (
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto :failed
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m quota_widget
exit /b 0
:failed
echo Setup failed. Please install Python 3.10+ and try again.
pause
exit /b 1
