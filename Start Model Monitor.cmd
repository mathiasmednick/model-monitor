@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 model_monitor.pyw
) else (
  python model_monitor.pyw
)
if errorlevel 1 (
  echo.
  echo Model Monitor could not start. Install Python 3 with Tkinter from python.org.
  pause
)
