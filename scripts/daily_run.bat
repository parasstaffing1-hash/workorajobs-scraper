@echo off
cd /d C:\Users\Administrator\Documents\ATS
echo [%date% %time%] Daily scrape starting >> .freebuff\daily_run.log
.venv\Scripts\pythonw.exe scripts\daily_scraper.py >> .freebuff\daily_run.log 2>&1
echo [%date% %time%] Daily scrape finished with exit code %ERRORLEVEL% >> .freebuff\daily_run.log
