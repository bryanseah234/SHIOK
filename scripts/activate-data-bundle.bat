@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0activate-data-bundle.ps1" %*
exit /b %ERRORLEVEL%
