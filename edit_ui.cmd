@echo off
cd /d "%~dp0"
py -3 -m lc_packer.designer
if errorlevel 1 pause
