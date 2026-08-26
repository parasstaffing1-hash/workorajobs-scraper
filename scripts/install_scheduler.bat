@echo off
echo ==========================================
echo  Workora Jobs - 24/7 Scheduler Setup
echo ==========================================
echo.

set SCRIPT_DIR=%~dp0
set PYTHON=%SCRIPT_DIR%..venvScriptspython.exe
set SCHEDULER=%SCRIPT_DIR%scheduler_24_7.py

echo [1/4] Installing Python scheduler...

REM Create the scheduler script if not exists
if not exist "%SCHEDULER%" (
    echo ERROR: scheduler_24_7.py not found
    pause
    exit /b 1
)

echo [2/4] Removing old scheduled tasks...
schtasks /delete /tn "Workora_Scraper_6H" /f 2>nul
schtasks /delete /tn "Workora_Alerts" /f 2>nul
schtasks /delete /tn "Workora_Health" /f 2>nul
schtasks /delete /tn "Workora_Scheduler" /f 2>nul

echo [3/4] Creating scheduled tasks...

REM Main scheduler - runs every 10 minutes, handles everything
schtasks /create /tn "Workora_Scheduler" /tr "\"%PYTHON%\" \"%SCHEDULER%\"" /sc minute /mo 10 /st 00:00 /f

REM Scraper task - runs every 6 hours
schtasks /create /tn "Workora_Scraper" /tr "\"%PYTHON%\" \"%SCHEDULER%\" --scraper-only" /sc hourly /st 00:00 /f

REM Alert check - runs every hour
schtasks /create /tn "Workora_Alerts" /tr "\"%PYTHON%\" \"%SCHEDULER%\" --alerts-only" /sc hourly /f

echo [4/4] Starting scheduler...
schtasks /run /tn "Workora_Scheduler"

echo.
echo ==========================================
echo  Scheduler Installed Successfully!
echo ==========================================
echo.
echo Tasks created:
echo   - Workora_Scheduler (every 10 min)
echo   - Workora_Scraper (every 6 hours)
echo   - Workora_Alerts (every hour)
echo.
echo To view tasks: schtasks /query /tn "Workora_*"
echo To remove tasks: schtasks /delete /tn "Workora_Scheduler" /f
echo.
echo To run manually:
echo   %PYTHON% %SCHEDULER% --once
echo   %PYTHON% %SCHEDULER% --scraper-only
echo   %PYTHON% %SCHEDULER% --stats
echo.
pause
