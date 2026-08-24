@echo off
setlocal
rem ---------------------------------------------------------------------
rem  Build the service environment OUTSIDE the app folder.
rem
rem  An installer wipes the app folder before writing the new version, so a
rem  venv living inside it is destroyed on every update -- and deleting several
rem  gigabytes of PyTorch is what makes an update take minutes. A venv in
rem  %APPDATA%\Toolbox\service survives updates and is picked up automatically.
rem
rem  Run this once. Re-run it any time to add the optional extras.
rem ---------------------------------------------------------------------

set "TARGET=%APPDATA%\Toolbox\service"
set "PY=%TARGET%\Scripts\python.exe"

where python >nul 2>&1 || (
    echo Python 3.11+ is required and was not found on PATH.
    echo   winget install Python.Python.3.12
    exit /b 1
)

if not exist "%PY%" (
    echo Creating the service environment in:
    echo   %TARGET%
    python -m venv "%TARGET%" || exit /b 1
)

echo.
echo Installing the core requirements...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r "%~dp0requirements.txt" || exit /b 1

echo.
echo ============================================================
echo  Core install finished. The app will use this environment.
echo ============================================================
echo.
echo Optional extras, each safe to skip:
echo.
echo  Transcribe videos that have no captions (~500MB model on first use):
echo    "%PY%" -m pip install faster-whisper
echo.
echo  Read the source page for tags and top comments:
echo    "%PY%" -m pip install playwright
echo    "%PY%" -m playwright install chromium
echo.
echo  Local narration - unlimited, free, and the only way to bulk-narrate
echo  Burmese. --no-deps is required: a plain install fails building kaldifst,
echo  which VoxCPM never imports.
echo    "%PY%" -m pip install voxcpm --no-deps
echo    "%PY%" -m pip install torch torchaudio transformers safetensors huggingface-hub soundfile librosa einops inflect addict tqdm pydantic
echo.
echo  Then swap in the CUDA build so it runs on the GPU rather than the CPU:
echo    "%PY%" -m pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126
echo.
where ffmpeg >nul 2>&1 || (
    echo WARNING: ffmpeg was not found on PATH. Install it with:
    echo   winget install Gyan.FFmpeg
    echo.
)
endlocal
