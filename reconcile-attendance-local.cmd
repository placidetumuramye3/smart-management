@echo off
setlocal

set "LOCAL_DATABASE_FILE=%~dp0instance\lyceedemuhura.db"
if not exist "%LOCAL_DATABASE_FILE%" (
    echo Local SQLite database not found: "%LOCAL_DATABASE_FILE%"
    exit /b 1
)

rem Use the same local SQLite database as run-local.cmd for this process only.
set "DATABASE_URL=sqlite:///lyceedemuhura.db"
cd /d "%~dp0"
py -m flask --app run.py reconcile-daily-attendance
exit /b %ERRORLEVEL%
