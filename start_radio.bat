@echo off
setlocal
chcp 65001 >nul
title ETS2 Radio DJ Launcher

set "ROOT=%~dp0"
set "RADIO_HOST=0.0.0.0"
set "RADIO_ALLOW_LAN=1"
set "RADIO_CACHE_DIR=E:\ets2_radio_cache"
set "PYTHON="
for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYTHON set "PYTHON=py -3"
if not defined PYTHON for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON set "PYTHON=python"

if not defined PYTHON (
    echo Python not found in PATH.
    exit /b 1
)

set "SERVER_PID="
for /f "delims=" %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$c=Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if($c){Write-Output $c}"') do set "SERVER_PID=%%P"

if defined SERVER_PID (
    echo [1/2] Server already listening on port 5000 ^(PID %SERVER_PID%^)
) else (
    echo [1/2] Starting server...
    start "" /min %PYTHON% "%ROOT%server.py"
    timeout /t 3 /nobreak >nul
)

echo [2/2] Starting SMTC bridge if needed...
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%smtc_bridge.ps1"

echo.
echo Done! Radio: http://127.0.0.1:5000
echo Game Bar: Win+G - media widget controls the radio.
echo This launcher and server close only with their own process windows.
timeout /t 5 /nobreak >nul
endlocal
