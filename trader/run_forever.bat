@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM run_forever.bat — Windows 24/7 bot watchdog (equivalent of run_forever.sh)
REM
REM Usage:
REM   run_forever.bat              Paper mode (default)
REM   run_forever.bat --live       Live trading
REM
REM Runs from the trader\ directory. Press Ctrl+C to stop.
REM ─────────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "ARGS=%*"
set "LOGFILE=trader.log"
set "RESTART_DELAY=5"
set "MAX_RESTARTS=1000"
set "RESTART_COUNT=0"

echo %date% %time% [WATCHDOG] Starting AI Trader 24/7 ^| args: %ARGS% >> "%LOGFILE%"
echo [WATCHDOG] Starting AI Trader 24/7 ^| args: %ARGS%

REM ── Load .env if present ────────────────────────────────────────────────────
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "%%A=%%B" 2>nul
    )
)

REM ── Launch Streamlit dashboard ──────────────────────────────────────────────
where streamlit >nul 2>&1
if not errorlevel 1 (
    start /b "" streamlit run dashboard.py --server.port 8501 --server.headless true >> trader_dashboard.log 2>&1
    echo [WATCHDOG] Dashboard starting at http://localhost:8501
)

REM ── Main loop ───────────────────────────────────────────────────────────────
:loop

REM Auto-update: pull latest code
git -C "%SCRIPT_DIR%.." rev-parse --is-inside-work-tree >nul 2>&1
if not errorlevel 1 (
    git -C "%SCRIPT_DIR%.." pull origin main >> "%LOGFILE%" 2>&1
)

echo %date% %time% [WATCHDOG] Launching bot... >> "%LOGFILE%"
python main.py %ARGS% 2>&1 >> "%LOGFILE%"

set /a RESTART_COUNT+=1

if %RESTART_COUNT% geq %MAX_RESTARTS% (
    echo %date% %time% [WATCHDOG] Max restarts reached. Stopping. >> "%LOGFILE%"
    echo [WATCHDOG] Max restarts reached. Stopping.
    exit /b 1
)

echo %date% %time% [WATCHDOG] Bot exited. Restart #%RESTART_COUNT% in %RESTART_DELAY%s... >> "%LOGFILE%"
echo [WATCHDOG] Bot exited. Restart #%RESTART_COUNT% in %RESTART_DELAY%s...
timeout /t %RESTART_DELAY% /nobreak >nul
goto loop
