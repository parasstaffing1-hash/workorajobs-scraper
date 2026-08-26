@echo off
cd /d C:\Users\Administrator\Documents\ATS
echo Starting scrapers at %date% %time% >> launch_log.txt

:: Kill old scrapers
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *scraper*" >NUL 2>&1
taskkill /F /IM pythonw.exe >NUL 2>&1
timeout /t 1 >NUL

:: Check if already running
tasklist /FI "IMAGENAME eq pythonw.exe" | find /I "pythonw.exe" >NUL 2>&1
if %ERRORLEVEL%==0 (
    echo Already running >> launch_log.txt
    exit /B 0
)

:: Launch V4 scraper (as python.exe for reliability)
start "scraper_v4" /B .venv\Scripts\python.exe -B scripts\mega_scraper_v4.py >> v4_output.txt 2>&1
echo V4 started at %time% >> launch_log.txt

:: Launch Hunter V2
start "scraper_hunter" /B .venv\Scripts\python.exe -B scripts\ats_hunter_v2.py >> hunter_output.txt 2>&1
echo HunterV2 started at %time% >> launch_log.txt

echo Both scrapers launched at %time% >> launch_log.txt
