@echo off
rem Double-click to open the טבלת התאמות report tool.
rem Uses pythonw so no black console window appears behind the app.
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw gui_app.py
) else (
    python gui_app.py
)
