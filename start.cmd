@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
start "Lecture Script" ".venv\Scripts\pythonw.exe" -m lecture_script
