@echo off
REM Launches the local web UI and opens it in your browser.
REM Run setup.cmd once first.
setlocal
if not exist "%~dp0venv\Scripts\python.exe" (
    echo venv not found. Run setup.cmd first.
    exit /b 1
)
set "PYTHONPATH=%~dp0"
"%~dp0venv\Scripts\python.exe" -m ytdl.web %*
