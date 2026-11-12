@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m reader --open %*
    goto finished
)
where.exe py >nul 2>nul
if not errorlevel 1 (
    py -3 -m reader --open %*
    goto finished
)
where.exe python >nul 2>nul
if not errorlevel 1 (
    python -m reader --open %*
    goto finished
)
echo Python 3.9 or newer was not found. Install Python and try again.
pause
exit /b 1

:finished
if errorlevel 1 (
    echo.
    echo The reader could not start. See the error above.
    pause
    exit /b 1
)
exit /b 0
