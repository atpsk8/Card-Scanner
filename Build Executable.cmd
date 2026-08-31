@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup Card Desk.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --name "Card Desk" --windowed --onefile --noconfirm main.py
if errorlevel 1 (
  echo Build failed. See the messages above.
  pause
  exit /b 1
)
copy /y "USER GUIDE.md" "dist\USER GUIDE.md" >nul
copy /y "README.md" "dist\README.md" >nul
echo Ready. "dist\Card Desk.exe" is a single-file app. Copy the whole dist folder so the guide travels with it.
echo The exe creates its own data, exports and backups folders next to wherever you place it.
pause
