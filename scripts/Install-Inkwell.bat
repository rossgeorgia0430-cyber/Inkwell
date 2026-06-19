@echo off
rem ===================================================================
rem  Inkwell installer launcher  (double-click to run)
rem  Per-user install, no admin required. Runs install.ps1 next to it.
rem  Tip: to force the .md default via Group Policy on a managed PC,
rem       right-click -> Run as administrator, it passes -SetDefaultViaPolicy.
rem ===================================================================
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel%==0 (
  rem elevated: also try the policy-based permanent default
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -SetDefaultViaPolicy %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
)

echo.
echo Done. Press any key to close.
pause >nul
