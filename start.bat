@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0chrome_console_logger.py" %*
) else (
  py "%~dp0chrome_console_logger.py" %*
)
pause
