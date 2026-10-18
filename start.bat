@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
    echo.
    echo Nie udalo sie uruchomic Tapo Web. Sprawdz komunikat powyzej.
    pause
    exit /b 1
)

if not defined TAPO_WEB_PORT set "TAPO_WEB_PORT=8088"
start "" "http://localhost:%TAPO_WEB_PORT%/"
exit /b 0
