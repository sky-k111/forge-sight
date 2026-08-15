@echo off
rem ForgeSight launcher. Usage: fs.bat [-Backend|-Frontend|-Db|-SkipDb|-Stop]
where pwsh >nul 2>nul && (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\forge-sight.ps1" %*
) || (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\forge-sight.ps1" %*
)
