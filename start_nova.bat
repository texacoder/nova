@echo off
REM Double-click this file to start NOVA on Windows.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py main.py %*
) else (
    python main.py %*
)
if errorlevel 1 pause
