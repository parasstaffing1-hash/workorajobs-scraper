"""Built-in daily scheduler.

Runs the collection tasks (collect, feed, scrape, report) on a clock so the
pipeline works without n8n, cron, or a manual trigger. Uses APScheduler's
blocking scheduler; each task runs as its own subprocess so a crash in one
source never takes down the others or the loop.

    jobcollect schedule --time 09:00 --tasks collect,feed,scrape
    jobcollect schedule --once                     # run the day's tasks now
    jobcollect schedule --install-task             # Windows daily task at 09:00
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

#: Tasks the scheduler knows how to run, in a sensible daily order.
KNOWN_TASKS = ("collect", "browser", "surf", "master", "web", "feed", "scrape", "report")

#: Human labels shown in the console/log.
TASK_LABELS = {
    "collect": "collect (boards/ATS/RSS/careers)",
    "browser": "browser-scrape (Indeed/LinkedIn/Glassdoor via Playwright)",
    "surf": "surf (web-search fresh jobs per searches.yaml)",
    "master": "master-scraper (JobSpy LinkedIn 200+ / apna / Shine / Indeed / Naukri)",
    "web": "web-scrape (Google Jobs/Jooble/Monster/Dice/SimplyHired/Wellfound/BuiltIn/Naukri)",
    "feed": "feed fetch (RSS reader)",
    "scrape": "scrape (yaml-defined targets)",
    "report": "report (regenerate dashboard.html)",
}


def parse_time(value: str) -> time:
    """Parse 'HH:MM' (or 'HH:MM:SS') into a datetime.time."""
    parts = [p for p in value.split(":") if p != ""]
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid time {value!r}: expected HH:MM")
    try:
        nums = [int(p) for p in parts]
        t = time(*nums)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid time {value!r}: expected HH:MM") from None
    if t.hour > 23 or t.minute > 59 or t.second > 59:
        raise ValueError(f"Invalid time {value!r}: expected HH:MM")
    return t


def resolve_tasks(tasks: str) -> list[str]:
    """Parse a comma-separated task list, validating against KNOWN_TASKS."""
    names = [t.strip() for t in tasks.split(",") if t.strip()]
    if not names:
        raise ValueError("No tasks given (e.g. --tasks collect,feed,scrape)")
    unknown = [n for n in names if n not in KNOWN_TASKS]
    if unknown:
        raise ValueError(
            f"Unknown task(s): {', '.join(unknown)}. Valid: {', '.join(KNOWN_TASKS)}"
        )
    # De-duplicate while preserving order.
    return list(dict.fromkeys(names))


def task_argv(
    task: str,
    *,
    config: str,
    feeds: str,
    scrapers: str,
    db: str,
) -> list[str]:
    """Build the subprocess argv for one task (python -m jobcollector.cli ...)."""
    base = [sys.executable, "-m", "jobcollector.cli"]
    if task == "collect":
        return base + ["collect", "--config", config, "--db", db]
    if task == "browser":
        # Only the 3 boards verified to work headlessly (fast, free, no keys):
        # Indeed, LinkedIn, Glassdoor. 30/job per keyword keeps a daily run ~3 min.
        return base + [
            "browser-scrape", "--boards", "indeed,linkedin,glassdoor",
            "--keywords", "software engineer,backend engineer,data engineer",
            "--db", db, "--limit", "30",
        ]
    if task == "surf":
        # Web-surf sweep: browses LinkedIn/Indeed/Shine/apna for every saved
        # search in searches.yaml and stores jobs posted within the window.
        return [
            sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "surf_fresh_jobs.py"),
            "--searches", str(Path(__file__).resolve().parent.parent / "searches.yaml"),
            "--db", db,
        ]
    if task == "master":
        # Master scraper: JobSpy (LinkedIn 200+/search) + surf (apna/Shine/Indeed)
        # Combines every free source for maximum daily job coverage.
        return [
            sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "master_scraper.py"),
            "--searches", str(Path(__file__).resolve().parent.parent / "searches.yaml"),
            "--db", db,
        ]
    if task == "web":
        # Multi-site web scraper: Google Jobs, Jooble, Monster, Dice,
        # SimplyHired, Wellfound, BuiltIn, Naukri, LinkedIn pagination,
        # Indeed pagination. Bypasses all paid APIs.
        return [
            sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "scrape_web_jobs.py"),
            "--query", "Software Engineer,Backend Engineer,Data Engineer,DevOps Engineer,React Developer",
            "--location", "Delhi,Bengaluru,Mumbai,Hyderabad,Pune,Chennai",
            "--hours", "48",
            "--db", db,
        ]
    if task == "feed":
        return base + ["feed", "fetch", "--db", db, "--config", feeds]
    if task == "scrape":
        return base + ["scrape", "--config", scrapers, "--db", db]
    if task == "report":
        return base + ["report", "--db", db]
    raise ValueError(f"Unknown task: {task}")


def next_fire(at: time, now: datetime | None = None) -> datetime:
    """Next datetime at which the daily time `at` occurs after `now`."""
    now = now or datetime.now()
    candidate = now.replace(hour=at.hour, minute=at.minute, second=at.second or 0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def run_tasks(
    tasks: list[str],
    *,
    config: str,
    feeds: str,
    scrapers: str,
    db: str,
    log_path: str | Path = "logs/schedule.log",
) -> dict[str, tuple[bool, str]]:
    """Run each task as a subprocess, appending output to the log.

    One task failing (bad network, upstream error, crash) does not stop the
    others. Returns {task: (ok, summary)}.
    """
    log = Path(log_path)
    results: dict[str, tuple[bool, str]] = {}
    for task in tasks:
        started = datetime.now()
        _log(log, f"\n[{started:%Y-%m-%d %H:%M:%S}] === {task} ({TASK_LABELS.get(task, task)}) ===\n")
        try:
            proc = subprocess.run(
                task_argv(task, config=config, feeds=feeds, scrapers=scrapers, db=db),
                capture_output=True,
                text=True,
            )
            output = proc.stdout or ""
            if proc.returncode != 0:
                output += (proc.stderr or "")[:4000]
            _log(log, output)
            elapsed = (datetime.now() - started).total_seconds()
            ok = proc.returncode == 0
            status = f"{task} {'OK' if ok else 'FAILED'} ({elapsed:.1f}s, exit {proc.returncode})"
            results[task] = (ok, status)
        except Exception as exc:  # subprocess spawn failure etc. — never kill the loop
            elapsed = (datetime.now() - started).total_seconds()
            status = f"{task} FAILED ({elapsed:.1f}s, {exc})"
            _log(log, f"{exc}\n")
            results[task] = (False, status)
        _log(log, f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {results[task][1]}\n")
    return results


TASK_NAME = "JobCollector Daily"
TASK_BAT = "schedule-daily.bat"


def install_windows_task(
    at: time,
    *,
    log_path: str,
    config: str = "companies.yaml",
    feeds: str = "feeds.yaml",
    scrapers: str = "scrapers.yaml",
    db: str = "jobs.db",
    bat_dir: str | Path | None = None,
) -> str:
    """Register a Windows Scheduled Task that runs the daily collection.

    The task itself just calls `jobcollect schedule --once`, so the OS owns
    the clock and the loop only lives for the duration of one run. A small
    .bat wrapper (baked with absolute paths) is written into the project so
    the task works regardless of the cwd Task Scheduler starts it from, and
    stays under schtasks' 261-char /TR limit.
    """
    if not sys.platform.startswith("win"):
        raise RuntimeError("--install-task is only available on Windows")
    if shutil.which("schtasks") is None:
        raise RuntimeError("schtasks not found on PATH")
    project = Path(bat_dir) if bat_dir else Path(__file__).resolve().parent.parent
    bat = project / TASK_BAT
    # newline="" disables Windows newline translation, so the explicit \r\n
    # endings are written exactly once (avoiding \r\r\n corruption).
    with bat.open("w", encoding="utf-8", newline="") as fh:
        fh.write(
            "@echo off\r\n"
            f'cd /d "{project}"\r\n'
            f'"{sys.executable}" -m jobcollector.cli schedule --once '
            f'--config "{config}" --feeds "{feeds}" --scrapers "{scrapers}" '
            f'--db "{db}" --log "{log_path}"\r\n'
        )
    st = f"{at.hour:02d}:{at.minute:02d}"
    # Escape inner quotes for schtasks (it does not strip them itself).
    tr = str(bat).replace('"', r'\"')
    cmd = [
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/SC", "DAILY",
        "/ST", st,
        "/TR", tr,
        "/F",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"schtasks failed: {proc.stderr or proc.stdout}")
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def remove_windows_task() -> None:
    """Delete the 'JobCollector Daily' scheduled task if it exists."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("--remove-task is only available on Windows")
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )





def run_loop(
    tasks: list[str],
    *,
    at: time,
    config: str,
    feeds: str,
    scrapers: str,
    db: str,
    log_path: str,
) -> None:
    """Block forever, running the tasks once per day at `at` (local time)."""
    scheduler = BlockingScheduler()
    first = next_fire(at)

    def _daily_job() -> None:
        _log(Path(log_path), f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] ===== DAILY RUN START =====\n")
        results = run_tasks(tasks, config=config, feeds=feeds, scrapers=scrapers, db=db, log_path=log_path)
        for task in tasks:
            ok, status = results[task]
            print(f"  [{'green' if ok else 'red'}]{status}[/]")
        _log(Path(log_path), f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ===== DAILY RUN END =====\n")

    scheduler.add_job(
        _daily_job,
        CronTrigger(hour=at.hour, minute=at.minute, second=at.second or 0),
        id="daily",
        misfire_grace_time=3600,
    )
    print(f"Scheduler started: {', '.join(tasks)} daily at {at:%H:%M} "
          f"(next run {first:%Y-%m-%d %H:%M}, log {log_path})")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")
