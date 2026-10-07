@echo off
setlocal
cd /d "%~dp0"
title EventFlow
if not exist "venv\Scripts\python.exe" (
  echo   First run detected - running setup...
  call setup.bat
  if not exist "venv\Scripts\python.exe" exit /b 1
)

rem ---- settings (edit these if you like)
if not defined PORT set "PORT=5000"
rem  HOST=0.0.0.0 lets phones on the same Wi-Fi open http://<this-PC-IP>:5000
if not defined HOST set "HOST=0.0.0.0"
rem  set ANTHROPIC_API_KEY=sk-ant-...   (optional: Claude-powered assistant)

echo.
echo   EventFlow is starting on http://localhost:%PORT%
echo   Keep this window open. Press Ctrl+C to stop.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:%PORT%"
"venv\Scripts\python.exe" app.py
echo.
echo   EventFlow stopped.
pause
