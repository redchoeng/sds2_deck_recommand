@echo off
cd /d "%~dp0"
call venv311\Scripts\activate
start "" pythonw overlay.py
