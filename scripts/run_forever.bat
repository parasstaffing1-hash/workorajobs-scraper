@echo off
:: Infinite loop launcher for mega_scraper
:: Each iteration runs for 5 minutes, saves checkpoint, exits
:: This bat relaunches it immediately
cd /d C:\Users\Administrator\Documents\ATS
:loop
echo [%date% %time%] Starting batch...
".venv\Scripts\pythonw.exe" scripts\mega_scraper.py --hours 0.083
echo [%date% %time%] Batch done, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
