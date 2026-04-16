@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM setup_windows.bat — One-click Windows setup for model-pep AI Trader
REM
REM Usage:
REM   1. Open Command Prompt or PowerShell
REM   2. cd C:\path\to\model-pep
REM   3. setup_windows.bat
REM
REM Prerequisites:
REM   - Python 3.11+  (winget install Python.Python.3.11)
REM   - Git for Windows (winget install Git.Git)
REM ─────────────────────────────────────────────────────────────────────────────

echo ============================================
echo  AI Trader — Windows Setup
echo ============================================
echo.

REM ── Check Python ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install it first:
    echo   winget install Python.Python.3.11
    exit /b 1
)
echo [OK] Python found

REM ── Create venv if missing ──────────────────────────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
) else (
    echo [OK] Virtual environment exists
)

REM ── Activate venv ───────────────────────────────────────────────────────────
call venv\Scripts\activate.bat
echo [OK] Virtual environment activated

REM ── Upgrade pip ─────────────────────────────────────────────────────────────
echo [SETUP] Upgrading pip...
python -m pip install --upgrade pip >nul 2>&1

REM ── Install requirements ────────────────────────────────────────────────────
echo [SETUP] Installing dependencies (this may take a few minutes)...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements. Check the output above.
    exit /b 1
)
echo [OK] Core dependencies installed

REM ── Install ccxt (Binance Futures) ──────────────────────────────────────────
echo [SETUP] Installing ccxt (exchange trading)...
pip install ccxt >nul 2>&1
echo [OK] ccxt installed

REM ── Check for .env ──────────────────────────────────────────────────────────
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [SETUP] Created .env from .env.example — edit it with your API keys
    ) else (
        echo [WARN] No .env file found. Create one with your API keys before trading.
    )
) else (
    echo [OK] .env file exists
)

echo.
echo ============================================
echo  Setup complete!
echo ============================================
echo.
echo  Quick commands (run from model-pep folder):
echo    python trader\main.py --scan     Scan for signals
echo    python trader\main.py            Paper trading
echo    python trader\main.py --live     Live trading
echo    python trader\main.py --status   Portfolio status
echo    streamlit run trader\dashboard.py   Web dashboard
echo.
echo  NOTE: Always activate venv first:
echo    .\venv\Scripts\activate
echo.
