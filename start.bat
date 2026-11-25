@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

echo ==================================================
echo   FreeAI Hub - 100%% Free Multi-Model AI ^& Voice
echo ==================================================

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create .venv. Please verify Python 3.10+ is installed.
        pause
        exit /b 1
    )
    echo [2/3] Installing required packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo.
echo Local Windows URL : http://localhost:8000
for /f "delims=" %%i in ('".venv\Scripts\python.exe" -c "from backend.config import get_lan_ips; print(', '.join('http://' + ip + ':8000' for ip in get_lan_ips()) or 'No LAN IP detected')"') do (
    echo Mobile Wi-Fi URL  : %%i
)
echo.
echo Press Ctrl+C in this window to stop the server.
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
