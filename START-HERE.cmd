@echo off
setlocal
title Lecture Script - Windows Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
set "lecture_result=%errorlevel%"
if not "%lecture_result%"=="0" echo Setup failed. See the error above and run START-HERE.cmd again.
pause
exit /b %lecture_result%
