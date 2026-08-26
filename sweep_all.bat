@echo off
cd /d C:\Users\Administrator\Documents\ATS
echo [%date% %time%] Sweep started >> .freebuff\sweep.log
.venv\Scripts\pythonw.exe -u scripts\master_scraper.py --searches searches.yaml --sources jobspy,surf,simplyhired,dice >> .freebuff\sweep.log 2>&1
echo [%date% %time%] Sweep finished with exit code %ERRORLEVEL% >> .freebuff\sweep.log
