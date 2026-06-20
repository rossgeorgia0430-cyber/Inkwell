@echo off
rem ===================================================================
rem  Inkwell installer launcher  (double-click to run)
rem  Per-user install, no admin required. Runs install.ps1 next to it.
rem  The installer only registers Inkwell as a candidate app. Windows asks the
rem  user before changing an existing default application.
rem ===================================================================
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*

echo.
echo Done. Press any key to close.
pause >nul
