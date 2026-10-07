@echo off
setlocal
cd /d "%~dp0"
title EventFlow setup
echo.
echo   ============================================
echo     EventFlow - one-time setup
echo   ============================================
echo.

rem ---- find a real Python 3.9+ (the "py" launcher first, then python on PATH)
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   Python was not found.
  echo   Install Python 3.10 or newer from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during setup. Then run setup.bat again.
  echo.
  pause
  exit /b 1
)
for /f "tokens=*" %%v in ('%PY% --version') do echo   Using %%v

rem ---- virtual environment
if not exist "venv\Scripts\python.exe" (
  echo   Creating a private Python environment in .\venv ...
  %PY% -m venv venv
  if errorlevel 1 (
    echo   Could not create the virtual environment.
    pause
    exit /b 1
  )
)

echo   Installing packages (Flask, qrcode) ...
"venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
"venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 (
  echo.
  echo   Package install failed. Check your internet connection and run setup.bat again.
  pause
  exit /b 1
)

echo.
echo.
echo   Setup complete. Double-click run.bat to start EventFlow.
echo.
pause
