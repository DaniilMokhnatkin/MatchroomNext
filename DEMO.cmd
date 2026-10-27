@echo off
setlocal
cd /d "%~dp0"
set "NEXT_ENTRY=demo.py"
set "NEXT_ARGS="
call "%~dp0launch.cmd" %*
if errorlevel 1 pause
endlocal
