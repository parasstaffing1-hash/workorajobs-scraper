#!/usr/bin/env python3
"""
Production Daemon Runner — runs scrapers in infinite loop with auto-restart.
Survives process crashes. Saves checkpoints. Logs everything.

Usage:
    python -m scripts.daemon_runner          # Run all scrapers
    python -m scripts.daemon_runner --scrapers ultra_v3  # Run specific
    python -m scripts.daemon_runner --api-only  # Run API server only
"""
from __future__ import annotations
import subprocess
import sys
import time
import json
import os
import signal
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(Path('.venv/Scripts/python.exe').resolve())
LOG_DIR = ROOT / 'logs'
STATE_FILE = ROOT / 'daemon_state.json'

SCRAPERS = {
    'ultra_v3': 'scripts.ultra_v3',
    'ats_hunter_v3': 'scripts.ats_hunter_v3',
}

API_SERVER = 'scripts.api_server'

class Daemon:
    def __init__(self):
        LOG_DIR.mkdir(exist_ok=True)
        self.state = {'started': datetime.now().isoformat(), 'restarts': 0, 'errors': []}
        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)
    
    def _shutdown(self, *args):
        self._log("Shutdown signal received, stopping...")
        self._running = False
    
    def _log(self, msg):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] [DAEMON] {msg}"
        print(line, flush=True)
        try:
            log_file = LOG_DIR / f"daemon_{datetime.now().strftime('%Y%m%d')}.log"
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except:
            pass
    
    def _save_state(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
        except:
            pass
    
    def _run_scraper(self, name: str, module: str):
        self._log(f"Starting {name}...")
        log_file = LOG_DIR / f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
        try:
            proc = subprocess.Popen(
                [PYTHON, '-m', module],
                cwd=str(ROOT),
                stdout=open(log_file, 'a'),
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self._log(f"  PID: {proc.pid}")
            return proc
        except Exception as e:
            self._log(f"  FAILED to start {name}: {e}")
            return None
    
    def _kill_port(self, port):
        """Kill any process using the given port."""
        try:
            import subprocess
            result = subprocess.run(
                ['netstat', '-ano'], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                if f':{port}' in line and 'LISTENING' in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(['taskkill', '/F', '/PID', pid], 
                                 capture_output=True, timeout=5)
                    self._log(f"  Killed process {pid} on port {port}")
        except:
            pass

    def _run_api(self):
        self._log("Starting API server on port 8000...")
        self._kill_port(8000)
        import time
        time.sleep(1)
        log_file = LOG_DIR / f"api_{datetime.now().strftime('%Y%m%d')}.log"
        try:
            proc = subprocess.Popen(
                [PYTHON, '-m', 'uvicorn', 'scripts.api_server:app', '--host', '0.0.0.0', '--port', '8000'],
                cwd=str(ROOT),
                stdout=open(log_file, 'a'),
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            self._log(f"  PID: {proc.pid}")
            return proc
        except Exception as e:
            self._log(f"  FAILED to start API: {e}")
            return None
    
    def _get_db_count(self):
        try:
            import sqlite3
            c = sqlite3.connect(str(ROOT / 'jobs.db'), timeout=5)
            count = c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            c.close()
            return count
        except:
            return -1
    
    def run_all(self):
        self._log("=" * 60)
        self._log("DAEMON STARTING")
        self._log(f"Python: {PYTHON}")
        self._log(f"DB: {self._get_db_count():,} jobs")
        self._log("=" * 60)
        
        processes = {}
        max_restarts = 50
        
        # Start all scrapers
        for name, module in SCRAPERS.items():
            proc = self._run_scraper(name, module)
            if proc:
                processes[name] = {'proc': proc, 'restarts': 0, 'last_start': time.time()}
        
        # Start API server
        api_proc = self._run_api()
        
        # Monitor loop
        last_count = self._get_db_count()
        last_report = time.time()
        
        while self._running:
            time.sleep(60)  # Check every minute
            
            # Check if scrapers died and restart them
            for name, info in list(processes.items()):
                proc = info['proc']
                if proc.poll() is not None:
                    exit_code = proc.returncode
                    self._log(f"{name} exited with code {exit_code}")
                    
                    if info['restarts'] < max_restarts:
                        info['restarts'] += 1
                        self.state['restarts'] += 1
                        self._log(f"Restarting {name} (attempt {info['restarts']})...")
                        time.sleep(5)  # Brief pause before restart
                        
                        # Clear checkpoint if crashed (start fresh)
                        cp_file = ROOT / f"{name}_cp.json"
                        if cp_file.exists():
                            try:
                                cp_file.unlink()
                            except:
                                pass
                        
                        new_proc = self._run_scraper(name, SCRAPERS[name])
                        if new_proc:
                            processes[name] = {'proc': new_proc, 'restarts': info['restarts'], 'last_start': time.time()}
                    else:
                        self._log(f"ERROR: {name} exceeded max restarts ({max_restarts})")
                        del processes[name]
            
            # Check API server
            if api_proc and api_proc.poll() is not None:
                self._log(f"API server exited with code {api_proc.returncode}, restarting...")
                api_proc = self._run_api()
            
            # Status report every 5 minutes
            current_time = time.time()
            if current_time - last_report >= 300:
                current_count = self._get_db_count()
                elapsed = current_time - last_report
                rate = (current_count - last_count) / (elapsed / 60) if elapsed > 0 else 0
                self._log(f"STATUS | DB: {current_count:,} | Rate: {rate:.0f}/min | Restarts: {self.state['restarts']}")
                last_count = current_count
                last_report = current_time
                self._save_state()
        
        # Cleanup
        self._log("Shutting down all processes...")
        for name, info in processes.items():
            try:
                info['proc'].terminate()
                info['proc'].wait(timeout=10)
            except:
                try:
                    info['proc'].kill()
                except:
                    pass
        
        if api_proc:
            try:
                api_proc.terminate()
                api_proc.wait(timeout=10)
            except:
                try:
                    api_proc.kill()
                except:
                    pass
        
        self._log("Daemon stopped.")
        self._save_state()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-only', action='store_true')
    parser.add_argument('--scrapers', nargs='+', default=list(SCRAPERS.keys()))
    args = parser.parse_args()
    
    if args.scrapers:
        SCRAPERS = {k: v for k, v in SCRAPERS.items() if k in args.scrapers}
    
    daemon = Daemon()
    daemon.run_all()
