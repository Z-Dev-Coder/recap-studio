@echo off
rem ===========================================================================
rem  Put Recap Studio on the desktop.
rem
rem  Makes a shortcut to RecapStudio.cmd that starts minimised, so the brief
rem  command window does not flash on screen -- the app window is the only
rem  thing that appears.
rem
rem  Run this once. Delete the shortcut whenever you like; nothing else
rem  depends on it.
rem ===========================================================================

setlocal
set HERE=%~dp0
set TARGET=%HERE%RecapStudio.cmd

if not exist "%TARGET%" (
  echo RecapStudio.cmd is not next to this file.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" ^
  "  [IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Recap Studio.lnk'));" ^
  "$s.TargetPath = '%TARGET%';" ^
  "$s.WorkingDirectory = '%HERE%';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = 'Burmese recap studio, running on this machine';" ^
  "$s.IconLocation = 'shell32.dll,177';" ^
  "$s.Save()"

if errorlevel 1 (
  echo The shortcut could not be created.
  pause
  exit /b 1
)

echo Done. "Recap Studio" is on your desktop.
echo.
echo   Double-click it and the app opens in its own window.
echo   The service starts by itself and keeps running until you sign out.
echo.
pause
