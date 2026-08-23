@echo off
REM Downloader entry point. Run setup.cmd once first.
REM Works from any directory -- paths are anchored to this script.
setlocal
if not exist "%~dp0venv\Scripts\python.exe" (
    echo venv not found. Run setup.cmd first.
    exit /b 1
)
REM PYTHONPATH so the `ytdl` package resolves even when the CWD is elsewhere.
set "PYTHONPATH=%~dp0"
"%~dp0venv\Scripts\python.exe" -m ytdl %*
