Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\ATS"
WshShell.Run "cmd /c .venv\Scripts\python.exe -m scripts.daemon_runner > logs\daemon.log 2>&1", 0, False
