@echo off
rem ForgeSight 一键启动/停止。用法：fs.bat [-Backend|-Frontend|-Db|-SkipDb|-Stop]
where pwsh >nul 2>nul && (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\forge-sight.ps1" %*
) || (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\forge-sight.ps1" %*
)
