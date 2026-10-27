@echo off
py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 goto launcher
set "NEXT_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%NEXT_PY%" goto bundled
python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 goto python
echo Matchroom Next requires Python 3.10 or newer.
echo Install Python from https://www.python.org/downloads/
exit /b 1
:launcher
py -3 -X utf8 "%~dp0%NEXT_ENTRY%" %NEXT_ARGS% %*
exit /b %errorlevel%
:bundled
"%NEXT_PY%" -X utf8 "%~dp0%NEXT_ENTRY%" %NEXT_ARGS% %*
exit /b %errorlevel%
:python
python -X utf8 "%~dp0%NEXT_ENTRY%" %NEXT_ARGS% %*
exit /b %errorlevel%
