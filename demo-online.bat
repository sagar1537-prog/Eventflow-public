@echo off
setlocal
cd /d "%~dp0"
title EventFlow live demo (Cloudflare Tunnel)
echo.
echo   Gives your local EventFlow a public https link through Cloudflare,
echo   so anyone (and any phone) can open the live demo. Free, no account needed.
echo.

set "CF="
where cloudflared >nul 2>nul && set "CF=cloudflared"
if not defined CF if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
if not defined CF if exist "%ProgramFiles%\cloudflared\cloudflared.exe" set "CF=%ProgramFiles%\cloudflared\cloudflared.exe"
if not defined CF (
  echo   cloudflared is not installed. Installing it with winget...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
  if exist "%ProgramFiles%\cloudflared\cloudflared.exe" set "CF=%ProgramFiles%\cloudflared\cloudflared.exe"
)
if not defined CF (
  echo.
  echo   Couldn't find cloudflared. Download it from
  echo   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  echo   then run this file again.
  pause
  exit /b 1
)

rem ---- start EventFlow in its own window
start "EventFlow server" cmd /k run.bat
timeout /t 5 >nul

echo.
echo   Look below for a line ending in  .trycloudflare.com  - that is your public demo link.
echo   Keep both windows open during the demo. Close them to stop.
echo.
"%CF%" tunnel --url http://localhost:5000
pause
