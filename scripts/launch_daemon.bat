@echo off
REM Production Daemon Launcher for Windows Task Scheduler
REM Runs the daemon runner which auto-manages all scrapers

cd /d "C:\Users\Administrator\Documents\ATS"

REM Log start
echo [%date% %time%] Daemon starting >> logs\boot.log

REM Run the daemon
.venv\Scripts\python.exe -m scripts.daemon_runner >> logs\daemon.log 2>&1
