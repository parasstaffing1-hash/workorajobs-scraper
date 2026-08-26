@echo off
cd /d "C:\Users\Administrator\Documents\ATS"
"C:\Users\Administrator\Documents\ATS\.venv\Scripts\python.exe" -m jobcollector.cli schedule --once --config "C:\Users\Administrator\Documents\ATS\companies.yaml" --feeds "C:\Users\Administrator\Documents\ATS\feeds.yaml" --scrapers "C:\Users\Administrator\Documents\ATS\scrapers.yaml" --db "C:\Users\Administrator\Documents\ATS\jobs.db" --log "C:\Users\Administrator\Documents\ATS\logs\schedule.log"
