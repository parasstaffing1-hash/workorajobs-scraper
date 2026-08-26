Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "pythonw -B scripts\loop_v4.py", 0, False
WScript.Quit