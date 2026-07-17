@echo off
rem Launch the report tool GUI. Double-click this file to open it.
rem pythonw hides the console window; falls back to python if absent.
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw gui_app.py
) else (
    python gui_app.py
)
