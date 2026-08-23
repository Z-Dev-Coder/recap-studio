@echo off
REM One-time setup: creates the venv and installs yt-dlp.
setlocal
cd /d "%~dp0"

where python >nul 2>&1 || (echo Python 3.11+ is required and was not found on PATH. & exit /b 1)

if not exist venv (
    echo Creating venv...
    python -m venv venv || exit /b 1
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || exit /b 1

where ffmpeg >nul 2>&1 || echo(& echo WARNING: ffmpeg not found on PATH. Install it with: winget install Gyan.FFmpeg

echo(
echo Done. Try:  ytdl https://youtu.be/jNQXAC9IVRw
