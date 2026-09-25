@echo off
setlocal
cd /d "%~dp0"
echo [hermes-director] Starting Hermes in %CD%
hermes -s multi-agent-dev-system-director %*
exit /b %ERRORLEVEL%
