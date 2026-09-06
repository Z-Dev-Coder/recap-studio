@echo off
rem ===========================================================================
rem  Recap Studio, without Electron.
rem
rem  Starts the local service if it is not already running and opens it in an
rem  app window -- a browser with no address bar, no tabs and its own taskbar
rem  button, which is what Electron was providing.
rem
rem  pythonw.exe rather than python.exe, so no console window is left behind.
rem  A fixed port, so the window can be pinned and the URL bookmarked; Electron
rem  chose a free port each launch, which is why no bookmark ever worked.
rem
rem  Double-click this, or use the desktop shortcut that
rem  "Make desktop shortcut.cmd" creates.
rem ===========================================================================

setlocal
set PORT=8756
set HERE=%~dp0
set URL=http://127.0.0.1:%PORT%/recap

rem --- the interpreter: the installed one first, then the repo's -----------
set PY=%APPDATA%\Toolbox\service\Scripts\pythonw.exe
if not exist "%PY%" set PY=%HERE%venv\Scripts\pythonw.exe
if not exist "%PY%" (
  echo Could not find the Python environment.
  echo Looked in:
  echo   %APPDATA%\Toolbox\service\Scripts\pythonw.exe
  echo   %HERE%venv\Scripts\pythonw.exe
  echo Run setup.cmd first.
  pause
  exit /b 1
)

rem --- already running? then just open it ----------------------------------
netstat -ano | findstr /r /c:"LISTENING.*:%PORT% " >nul 2>&1
if not errorlevel 1 goto open

rem --- start it -----------------------------------------------------------
rem  python.exe, not pythonw.exe. pythonw is the obvious tool and it does not
rem  work here: with no stdout handle the server dies on its first write.
rem  Measured both ways -- python.exe answers on the port, pythonw.exe never
rem  comes up, redirected or not.
rem
rem  The console window is hidden by the desktop shortcut, which is set to
rem  start minimised. That is one setting on a .lnk rather than a second
rem  scripting language in the launch path.
set PY=%APPDATA%\Toolbox\service\Scripts\python.exe
if not exist "%PY%" set PY=%HERE%venv\Scripts\python.exe
pushd "%HERE%"
start "Recap Studio service" /min "%PY%" -m ytdl.web -p %PORT%
popd

rem --- wait for it to answer, up to 40 seconds -----------------------------
set /a TRIES=0
:wait
set /a TRIES+=1
if %TRIES% gtr 40 (
  echo The service did not start. Run ytdl-ui.cmd to see why.
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
netstat -ano | findstr /r /c:"LISTENING.*:%PORT% " >nul 2>&1
if errorlevel 1 goto wait

:open
rem --- an app window, not a browser tab ------------------------------------
set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
set CHROME86=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe

if exist "%CHROME%"   start "" "%CHROME%"   --app=%URL% & exit /b 0
if exist "%CHROME86%" start "" "%CHROME86%" --app=%URL% & exit /b 0
if exist "%EDGE%"     start "" "%EDGE%"     --app=%URL% & exit /b 0

rem no Chromium browser: the default one, as an ordinary tab
start "" %URL%
exit /b 0
