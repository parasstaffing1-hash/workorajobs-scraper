@echo off
cd /d C:\Users\Administrator\Documents\ATS
echo Starting Mega Batch at %date% %time% > mega_batch_launch.log
.venv\Scripts\python.exe -u scripts\run_until_1m.py >> mega_batch_launch.log 2>&1
echo Finished at %date% %time% >> mega_batch_launch.log
