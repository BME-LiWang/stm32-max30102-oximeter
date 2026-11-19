@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
if not exist "%PY%" set PY=python
echo ------------------------------------------
echo  Mobile Web Monitor (default COM9 @9600)
echo  Close the desktop PC monitor first.
echo ------------------------------------------
"%PY%" web_monitor.py %*
echo.
pause