@echo off
rem Sets up this folder's Python environment (.venv) with the packages Hondana uses.
rem   Setup.cmd       reader packages: Anki colours, known-word coverage, generated furigana
rem   Setup.cmd ocr   also Mokuro, for turning manga volumes into readable pages (a large download: it includes PyTorch)
rem Run it again at any time to update the packages.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Creating the Python environment in .venv ...
    where.exe py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && (
        py -3 -m venv .venv
    ) || (
        python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && python -m venv .venv
    )
)
if not exist "%PY%" (
    echo Python 3.9 or newer was not found. Install it from https://www.python.org/ and run Setup.cmd again.
    goto failed
)

"%PY%" -m pip install --upgrade pip || goto failed
"%PY%" -m pip install --upgrade -r requirements-reader.txt || goto failed
if /i "%~1"=="ocr" (
    "%PY%" -m pip install --upgrade -r requirements-ocr.txt || goto failed
)

echo.
echo Setup finished. Double-click Start-Reader.cmd, then choose your folders under Settings.
if /i not "%~1"=="ocr" echo To also process manga with Mokuro, run: Setup.cmd ocr
pause
exit /b 0

:failed
echo.
echo Setup did not finish; see the messages above.
pause
exit /b 1
