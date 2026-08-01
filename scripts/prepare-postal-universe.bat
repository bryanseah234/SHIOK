@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare-postal-universe.ps1" %*
exit /b %ERRORLEVEL%
