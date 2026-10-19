@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0LC打包.exe" (
    start "" "%~dp0LC打包.exe"
    exit /b 0
)
py -3 -m lc_packer gui
if errorlevel 1 pause
