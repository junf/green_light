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
rem Test the arguments one at a time via `for`. Do NOT fold %* into a variable:
rem cmd expands %* before it parses special characters, so `set "ARGS=%*"` with a
rem quoted URL containing & ends up as set "ARGS="http://x/?a=1&b=2"" -- the URL's
rem own quotes close the set, the & escapes, and cmd runs `b=2""` as a command.
rem Each `for` element stays wrapped in the URL's quotes, which keeps & inert.
rem (`find` is not an option here: it resolves to the Unix find when Git Bash is
rem on PATH, which does not take these arguments.)
set "NOPAUSE="
for %%A in (%*) do (
  if /i "%%~A"=="--check"  set "NOPAUSE=1"
  if /i "%%~A"=="--doctor" set "NOPAUSE=1"
)
if not defined NOPAUSE pause
exit /b %RC%
