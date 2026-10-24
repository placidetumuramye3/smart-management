@echo off
setlocal

set "LOCAL_DATABASE_FILE=%~dp0instance\lyceedemuhura.db"
if not exist "%LOCAL_DATABASE_FILE%" (
    echo Local SQLite database not found: "%LOCAL_DATABASE_FILE%"
    exit /b 1
)

rem Use the existing local database for this process; do not change .env.
set "DATABASE_URL=sqlite:///lyceedemuhura.db"
echo Starting with local SQLite database: "%LOCAL_DATABASE_FILE%"
py "%~dp0run.py"
exit /b %ERRORLEVEL%
