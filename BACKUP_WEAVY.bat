@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  AI WORKFLOW ARCHIVER v1.5.1 - WINDOWS
echo ============================================================
echo.
echo Project backup folders will be created directly in:
echo %CD%
echo.

set "PYMODE="

where py >nul 2>nul
if not errorlevel 1 set "PYMODE=PY"

if not defined PYMODE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYMODE=PYTHON"
)

if not defined PYMODE (
    echo ERROR: Python 3 was not found.
    echo Install Python 3.10+ and make sure either "py" or "python" is available.
    echo.
    pause
    exit /b 1
)

if "%PYMODE%"=="PY" (
    py -3 "%~dp0archive_weavy.py" --interactive
) else (
    python "%~dp0archive_weavy.py" --interactive
)

set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo ============================================================
    echo  ARCHIVER STOPPED WITH AN ERROR
    echo ============================================================
    echo Exit code: %EXITCODE%
    echo.
    pause
)

exit /b %EXITCODE%
