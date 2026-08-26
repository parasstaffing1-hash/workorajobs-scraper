Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c .venv\Scripts\python.exe -m scripts.gap_filler > logs\gap_out.txt 2>&1", 0, False
