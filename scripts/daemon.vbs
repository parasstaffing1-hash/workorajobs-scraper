Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

ATSDir = "C:\Users\Administrator\Documents\ATS"
Python = ATSDir & "\.venv\Scripts\python.exe"
Script = ATSDir & "\scripts\batch_engine_v2.py"

WshShell.CurrentDirectory = ATSDir

' Write PID file
Set logFile = fso.CreateTextFile(ATSDir & "\daemon_pid.txt", True)
logFile.Write WshShell.Exec("cmd /c echo %PROCESS_ID%").StdOut.ReadAll()
logFile.Close

Do
    ' Run scraper for up to 50 minutes (well under any timeout)
    WshShell.Run """" & Python & """ -B """ & Script & """ --hours 0.8", 0, True
    
    ' Wait 10 seconds before relaunch
    WScript.Sleep 10000
    
    ' Check if we should stop
    If fso.FileExists(ATSDir & "\stop_daemon.txt") Then
        Exit Do
    End If
Loop
