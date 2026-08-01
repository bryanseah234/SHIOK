@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release-data-bundle.ps1" %*
exit /b %ERRORLEVEL%
