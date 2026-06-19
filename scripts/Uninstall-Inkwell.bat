@echo off
rem ===================================================================
rem  Inkwell uninstaller launcher  (double-click to run)
rem  Removes Inkwell, its registration and shortcuts. No admin needed.
rem  Run as administrator to also remove the Group Policy default (-RemovePolicy).
rem ===================================================================
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel%==0 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" -RemovePolicy %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
)

echo.
echo Done. Press any key to close.
pause >nul
