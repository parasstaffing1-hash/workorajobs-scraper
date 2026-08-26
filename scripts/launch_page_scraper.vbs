Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c .venv\Scripts\python.exe -B scripts\pagination_scraper.py > page_scraper_output.txt 2>&1", 0, False
WScript.Quit