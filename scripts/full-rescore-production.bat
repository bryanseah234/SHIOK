@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0full-rescore-production.ps1" %*
exit /b %ERRORLEVEL%
