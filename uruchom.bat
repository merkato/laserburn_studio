@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
start "" .venv\Scripts\pythonw.exe laserburn_studio.py
exit