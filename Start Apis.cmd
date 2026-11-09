@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0app.py"
  exit /b
)
where pyw.exe >nul 2>&1
if not errorlevel 1 (
  start "" pyw.exe -3 "%~dp0app.py"
  exit /b
)
echo Python with Tk is required. Install Python for Windows from python.org.
pause
