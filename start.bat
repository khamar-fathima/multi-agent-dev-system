@echo off
setlocal
cd /d "%~dp0"

REM Multi-Agent Software Development System launcher
REM Usage:
REM   start.bat                                  -> interactive (you type requirements)
REM   start.bat "build me a CLI tool that..."    -> requirements as first arg
REM   start.bat --requirements-file spec.md      -> requirements from a file
REM   start.bat --name my-project ...            -> name the project folder
REM   start.bat --max-iterations 8 ...           -> override fix-loop cap

set "PYTHONHOME="
set "UV_INTERNAL__PYTHONHOME="
set "PYTHONNOUSERSITE=1"
set "PY_CMD="

if exist "%CD%\.venv\Scripts\python.exe" (
    set "PY_CMD="%CD%\.venv\Scripts\python.exe""
    call :ValidatePython
    if not errorlevel 1 goto :PythonFound
)

for %%C in ("py -3.12" "py -3.11" "py -3" "python" "python3") do (
    set "PY_CMD=%%~C"
    call :ValidatePython
    if not errorlevel 1 goto :PythonFound
)

echo ERROR: Python 3.10+ was not found. Install Python or create .venv\Scripts\python.exe.
call :MaybePause
exit /b 1

:PythonFound
echo Using Python: %PY_CMD%

REM First-run dependency install (only if anthropic/openai are not importable yet)
%PY_CMD% -c "import anthropic, openai" >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies...
    %PY_CMD% -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed. Run "%PY_CMD% -m pip install -r requirements.txt" manually.
        call :MaybePause
        exit /b 1
    )
)

%PY_CMD% orchestrator.py %*
set RC=%ERRORLEVEL%

echo.
echo (pipeline exited with code %RC%)
call :MaybePause
exit /b %RC%

:ValidatePython
%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:MaybePause
if not "%MULTI_AGENT_NO_PAUSE%"=="1" pause
exit /b 0
