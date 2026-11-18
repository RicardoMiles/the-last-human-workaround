@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Repair-PptxMaker.ps1" %*
set "WORKAROUND_EXIT=%ERRORLEVEL%"
echo.
echo Workaround exit code: %WORKAROUND_EXIT%
pause
exit /b %WORKAROUND_EXIT%
