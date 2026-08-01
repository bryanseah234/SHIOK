@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0preflight-production.ps1" %*
exit /b %ERRORLEVEL%
