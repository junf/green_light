@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0chrome_console_logger.py" %*
) else (
  py "%~dp0chrome_console_logger.py" %*
)
rem Keep Python's exit code. A batch file otherwise exits with the status of its
rem last command, so `pause` would mask the --check result (0 = device reachable,
rem 1 = a stage failed, 2 = nothing was checked).
set "RC=%errorlevel%"
rem A capture ends when the user stops it, so hold the window open to show why.
rem --check is a one-shot report: let it return without waiting for a keypress.
rem Substring test via cmd's own string substitution -- `find` would resolve to the
rem Unix find when Git Bash / MSYS is on PATH, which does not take these arguments.
set "ARGS=%*"
if not defined ARGS set "ARGS=."
set "NOPAUSE="
if not "%ARGS%"=="%ARGS:--check=%" set "NOPAUSE=1"
if not "%ARGS%"=="%ARGS:--doctor=%" set "NOPAUSE=1"
if not defined NOPAUSE pause
exit /b %RC%
