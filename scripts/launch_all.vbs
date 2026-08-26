Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c .venv\Scripts\python.exe -B scripts\mega_scraper_v4.py", 0, False
WScript.Sleep 2000
WshShell.Run "cmd /c .venv\Scripts\python.exe -B scripts\ats_hunter_v2.py", 0, False
