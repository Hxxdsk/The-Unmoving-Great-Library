@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "启动工具箱.exe" (
    start "" "%~dp0启动工具箱.exe"
    exit /b 0
)
echo Native launcher is missing. Extract the complete distribution ZIP.
echo Source development: install requirements.txt and run suite_entry.py.
pause
exit /b 1
