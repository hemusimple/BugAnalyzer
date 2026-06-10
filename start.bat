@echo off
REM start.bat — Windows launcher

echo ═══════════════════════════════════
echo   Android Log Analyzer Agent
echo ═══════════════════════════════════

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org
    pause
    exit /b 1
)

REM Create venv
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

REM Install deps
echo Installing dependencies...
pip install -r requirements.txt -q

REM Check .env
if not exist ".env" (
    echo Creating .env from example...
    copy .env.example .env
    echo WARNING: Edit .env with your credentials
)

REM Create data dirs
if not exist "data\repos" mkdir data\repos
if not exist "data\logs" mkdir data\logs
if not exist "data\indexes" mkdir data\indexes

echo.
echo Starting server...
echo Open http://localhost:8000
echo.

python -m api.main
pause
