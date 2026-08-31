@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 or newer from python.org and enable Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo Could not create the Python environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Setup did not finish. Check your internet connection and Python installation.
  pause
  exit /b 1
)
echo Ready. Double-click Start Card Desk.cmd to open the app.
pause
