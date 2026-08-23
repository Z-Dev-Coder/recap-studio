@echo off
set ELECTRON_RUN_AS_NODE=
cd /d "%~dp0"
if not exist "node_modules\electron\dist\electron.exe" (
    echo Installing Electron ^(one time^)...
    call npm install --no-audit --no-fund || exit /b 1
)
start "" ".\node_modules\electron\dist\electron.exe" .
