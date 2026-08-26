@echo off
cd /d C:\Users\Administrator\Documents\ATS
start "" /b .venv\Scripts\pythonw.exe -B scripts\bulletproof_scraper.py --hours 8
echo Spawned
