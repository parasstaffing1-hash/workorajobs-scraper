Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c .venv\Scripts\python.exe -m uvicorn scripts.api_server:app --host 127.0.0.1 --port 8000", 0, False
