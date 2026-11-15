@echo off
setlocal
cd /d "%~dp0backend"

echo.
echo   life-ai
echo   -------
echo   Keep this window open while you use it. Closing it stops the app.
echo.

rem 1) find a python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo   [!] Python 3.11+ not found.
    echo       Install it from https://www.python.org/downloads/
    echo       and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

rem 2) first run: create the venv and install dependencies
if not exist ".venv\Scripts\python.exe" (
    echo   First run: creating .venv and installing dependencies...
    echo   This takes 1-2 minutes. Do not close this window.
    echo.
    %PY% -m venv .venv
    if errorlevel 1 (
        echo   [!] failed to create .venv
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo   [!] pip install failed - check your network
        pause
        exit /b 1
    )
    echo.
    echo   Dependencies installed.
    echo.
)

rem 3) .env is optional; copy the example on first run
if not exist ".env" if exist "..\.env.example" copy "..\.env.example" ".env" >nul

rem 4) open the browser shortly after starting
start "" /b cmd /c "timeout /t 4 >nul & start "" http://127.0.0.1:8000"

.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo   --- server stopped ---
echo   If you saw 10048 above, another backend is already running.
echo   Close that other window, then double-click this file again.
echo.
pause
