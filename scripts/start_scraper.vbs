Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c C:\Users\Administrator\Documents\ATS\.venv\Scripts\pythonw.exe -B C:\Users\Administrator\Documents\ATS\scripts\batch_engine_v2.py --hours 8", 0, False
WScript.Echo "Launched"
