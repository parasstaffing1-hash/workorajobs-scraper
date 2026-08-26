#!/usr/bin/env python3
"""Launch fast_scrape.py via WMI — survives parent death."""
import wmi
c = wmi.WMI()
result = c.Win32_Process.Create(
    CommandLine=r'cmd.exe /c cd /d C:\Users\Administrator\Documents\ATS && .venv\Scripts\python.exe -B scripts\fast_scrape.py --hours 0.13',
    CurrentDirectory=r'C:\Users\Administrator\Documents\ATS'
)
print(f"WMI create result: {result}")
