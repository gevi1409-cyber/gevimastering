@echo off
cd /d "%~dp0"
py -3 web_app.py
if errorlevel 1 pause
