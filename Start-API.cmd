@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\CodexQuota\CodexQuota.exe" (
    echo For console API mode use the Python source command below.
)
if not exist "%~dp0.venv\Scripts\python.exe" (
    python -m venv "%~dp0.venv"
    if errorlevel 1 goto :failed
)
"%~dp0.venv\Scripts\python.exe" -c "import curl_cffi" >nul 2>&1
if errorlevel 1 (
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto :failed
)
"%~dp0.venv\Scripts\python.exe" -m quota_widget --api-only
if errorlevel 1 pause
exit /b
:failed
echo Setup failed. Please install Python 3.10+ and try again.
pause
exit /b 1
