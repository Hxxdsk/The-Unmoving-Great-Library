@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0launcher"
where go >nul 2>nul
if errorlevel 1 (
    echo Install Go from https://go.dev/dl/ to rebuild the native launcher.
    echo End users can directly run the provided EXE without Go.
    pause
    exit /b 1
)
set GOOS=windows
set GOARCH=amd64
set CGO_ENABLED=0
go build -trimpath -ldflags "-H=windowsgui -s -w" -o "..\启动工具箱.exe" .
if errorlevel 1 (
    echo Build failed. Check the error above.
    pause
    exit /b 1
)
echo Build complete: 启动工具箱.exe
pause
