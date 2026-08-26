# Workora Jobs - Run Instructions

## How to start the server
```
cd C:\Users\Administrator\Documents\ATS
PYTHONPATH=. .venv\Scripts\python.exe -m uvicorn scripts.workora_app:app --host 0.0.0.0 --port 8000
```

## Key URLs
- Homepage: http://localhost:8000
- Jobs: http://localhost:8000/jobs
- API: http://localhost:8000/api/health
- Sitemap: http://localhost:8000/sitemap.xml
