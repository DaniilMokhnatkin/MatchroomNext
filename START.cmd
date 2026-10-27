@echo off
setlocal
cd /d "%~dp0"
set "NEXT_ENTRY=desktop_app.py"
set "NEXT_ARGS=--no-tray"
call "%~dp0launch.cmd" %*
if errorlevel 1 pause
endlocal
