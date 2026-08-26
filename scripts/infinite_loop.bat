@echo off
cd /d C:\Users\Administrator\Documents\ATS
:loop
echo [%date% %time%] Starting scraper round...
.venv\Scripts\python.exe -B scripts\batch_engine_v2.py --hours 0.13
echo [%date% %time%] Round ended, waiting 10s before relaunch...
timeout /t 10 /nobreak >nul
goto loop
