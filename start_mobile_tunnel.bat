@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo   FreeAI Hub - Free HTTPS Mobile Tunnel (Cloudflare)
echo ========================================================
echo Make sure start.bat is already running on port 8000!
echo.

where cloudflared >nul 2>nul
if errorlevel 1 (
    echo cloudflared not found on PATH.
    where winget >nul 2>nul
    if not errorlevel 1 (
        echo Attempting to install Cloudflare Tunnel CLI via winget...
        winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
    ) else (
        echo Please download cloudflared from:
        echo https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
        pause
        exit /b 1
    )
)

echo Opening free HTTPS URL for any phone (supports Live Voice mic)...
echo Copy the https://*.trycloudflare.com link shown below into your mobile browser.
echo Tip: Set a PIN in Settings -^> Security before sharing remote links!
echo.
cloudflared tunnel --url http://localhost:8000
pause
