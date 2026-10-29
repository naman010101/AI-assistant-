@echo off
rem Works from wherever the folder lives
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0app.py"
