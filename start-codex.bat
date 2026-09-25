@echo off
setlocal
cd /d "%~dp0"

REM Codex-only multi-agent launcher.
REM Usage:
REM   start-codex.bat
REM   start-codex.bat "Build a CLI todo app in Python with SQLite."
REM   start-codex.bat -RequirementsFile spec.md -Name todo-cli -MaxIterations 3

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0codex-team.ps1" %*
set RC=%ERRORLEVEL%

echo.
echo (codex team exited with code %RC%)
if not "%MULTI_AGENT_NO_PAUSE%"=="1" pause
exit /b %RC%
