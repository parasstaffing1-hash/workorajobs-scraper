Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c .venv\Scripts\python.exe -B scripts\mega_scraper_v4.py", 0, False
WScript.Sleep 2000
WshShell.Run "cmd /c .venv\Scripts\python.exe -B scripts\ats_hunter_v2.py", 0, False
WScript.Sleep 1000
' Write timestamp
Set fso = CreateObject("Scripting.FileSystemObject")
Set f = fso.OpenTextFile("C:\Users\Administrator\Documents\ATS\launch_log.txt", 8, True)
f.WriteLine "Launched at " & Now()
f.Close
