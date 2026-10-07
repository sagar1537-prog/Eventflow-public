@echo off
cd /d "%~dp0"
echo.
echo   This deletes ALL EventFlow data (accounts, events, uploads) and restores the fresh demo.
echo   (This is for the copy on this computer. For the Render site use Developer console - Reset demo data.)
echo   Stop EventFlow first (close the run.bat window).
echo.
set /p ok="  Type YES to continue: "
if /i not "%ok%"=="YES" (
  echo   Cancelled.
  pause
  exit /b 0
)
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" tools\reset_db.py --yes
) else (
  if exist instance rmdir /s /q instance
)
echo   Done. Run run.bat - the demo data is recreated on start.
pause
