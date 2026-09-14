@echo off
REM Launches the local web UI and opens it in your browser.
REM
REM The interpreter matters more than it looks. The venv beside this file is a
REM thin one -- the web server and little else -- so a service started on it
REM runs, answers, and then cannot narrate: no VoxCPM, no torch, no Playwright.
REM The environment Toolbox installed has all of them, so it is tried first.
setlocal
set "PY=%APPDATA%\Toolbox\service\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
    echo No Python environment found. Run setup.cmd first.
    pause
    exit /b 1
)
echo Using %PY%
set "PYTHONPATH=%~dp0"
"%PY%" -m ytdl.web %*
