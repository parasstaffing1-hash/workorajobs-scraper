"""Tests for the built-in daily scheduler (jobcollect schedule)."""
from __future__ import annotations

import subprocess
from datetime import datetime, time, timedelta

import pytest

from jobcollector import scheduler


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- time parsing -----------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("09:00", time(9, 0)),
    ("23:59", time(23, 59)),
    ("00:00", time(0, 0)),
    ("09:05:30", time(9, 5, 30)),
])
def test_parse_time_valid(value: str, expected: time) -> None:
    assert scheduler.parse_time(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "25:00", "9", "09:60", "09:00:61"])
def test_parse_time_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        scheduler.parse_time(value)


# --- task resolution --------------------------------------------------------

def test_resolve_tasks_valid_and_ordered() -> None:
    assert scheduler.resolve_tasks("collect,feed,scrape,report") == [
        "collect", "feed", "scrape", "report",
    ]


def test_resolve_tasks_dedupes_and_strips() -> None:
    assert scheduler.resolve_tasks(" collect , scrape, collect ") == ["collect", "scrape"]


def test_resolve_tasks_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown task"):
        scheduler.resolve_tasks("collect,bogus")


def test_resolve_tasks_empty() -> None:
    with pytest.raises(ValueError, match="No tasks"):
        scheduler.resolve_tasks("")


# --- next fire time ---------------------------------------------------------

def test_next_fire_later_today() -> None:
    now = datetime(2026, 1, 1, 8, 0, 0)
    assert scheduler.next_fire(time(9, 0), now) == datetime(2026, 1, 1, 9, 0, 0)


def test_next_fire_tomorrow_when_passed() -> None:
    now = datetime(2026, 1, 1, 10, 0, 0)
    assert scheduler.next_fire(time(9, 0), now) == datetime(2026, 1, 2, 9, 0, 0)


def test_next_fire_exact_now_rolls_to_tomorrow() -> None:
    now = datetime(2026, 1, 1, 9, 0, 0)
    assert scheduler.next_fire(time(9, 0), now) == datetime(2026, 1, 2, 9, 0, 0)


# --- task argv --------------------------------------------------------------

def test_task_argv_commands() -> None:
    argv = scheduler.task_argv("collect", config="c.yaml", feeds="f.yaml",
                               scrapers="s.yaml", db="d.db")
    assert argv[3:] == ["collect", "--config", "c.yaml", "--db", "d.db"]
    argv = scheduler.task_argv("feed", config="c.yaml", feeds="f.yaml",
                               scrapers="s.yaml", db="d.db")
    assert argv[3:] == ["feed", "fetch", "--db", "d.db", "--config", "f.yaml"]
    argv = scheduler.task_argv("scrape", config="c.yaml", feeds="f.yaml",
                               scrapers="s.yaml", db="d.db")
    assert argv[3:] == ["scrape", "--config", "s.yaml", "--db", "d.db"]
    argv = scheduler.task_argv("report", config="c.yaml", feeds="f.yaml",
                               scrapers="s.yaml", db="d.db")
    assert argv[3:] == ["report", "--db", "d.db"]


def test_task_argv_unknown() -> None:
    with pytest.raises(ValueError):
        scheduler.task_argv("bogus", config="c", feeds="f", scrapers="s", db="d")


# --- run_tasks: failure isolation + logging ---------------------------------

def test_run_tasks_isolates_failures(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if "scrape" in argv:
            return _Proc(returncode=1, stdout="boom\n", stderr="trace")
        return _Proc(returncode=0, stdout=f"{argv[3]} ok\n")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    log = tmp_path / "schedule.log"
    results = scheduler.run_tasks(
        ["collect", "scrape", "report"],
        config="c.yaml", feeds="f.yaml", scrapers="s.yaml", db="d.db",
        log_path=str(log),
    )
    # All three ran even though scrape failed.
    assert [c[3] for c in calls] == ["collect", "scrape", "report"]
    assert results["collect"][0] is True and "OK" in results["collect"][1]
    assert results["scrape"][0] is False
    assert "FAILED" in results["scrape"][1]
    assert results["report"][0] is True and "OK" in results["report"][1]

    text = log.read_text(encoding="utf-8")
    assert "=== collect" in text
    assert "collect ok" in text
    assert "boom" in text
    assert "collect OK" in text
    assert "scrape FAILED" in text


def test_run_tasks_crash_still_logs(tmp_path, monkeypatch) -> None:
    def fake_run(argv, **kw):
        raise OSError("spawn failed")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    log = tmp_path / "schedule.log"
    results = scheduler.run_tasks(
        ["feed"], config="c", feeds="f", scrapers="s", db="d", log_path=str(log)
    )
    assert results["feed"][0] is False
    assert "spawn failed" in log.read_text(encoding="utf-8")


def test_run_tasks_creates_log_dir(tmp_path) -> None:
    def fake_run(argv, **kw):
        return _Proc(returncode=0, stdout="done\n")

    scheduler.subprocess.run = fake_run
    log = tmp_path / "nested" / "dir" / "schedule.log"
    scheduler.run_tasks(["report"], config="c", feeds="f", scrapers="s", db="d",
                        log_path=str(log))
    assert log.exists()


# --- Windows Task Scheduler install -----------------------------------------

def test_install_windows_task_writes_bat_with_abs_paths(monkeypatch, tmp_path) -> None:
    called: list[list[str]] = []

    def fake_run(argv, **kw):
        called.append(argv)
        return _Proc(returncode=0)

    monkeypatch.setattr(scheduler, "sys", type("S", (), {"platform": "win32",
                                                          "executable": r"C:\proj\.venv\Scripts\python.exe"})())
    monkeypatch.setattr(scheduler.shutil, "which", lambda _: r"C:\Windows\System32\schtasks.exe")
    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    scheduler.install_windows_task(
        scheduler.time(9, 0),
        config=r"C:\proj\companies.yaml", feeds=r"C:\proj\feeds.yaml",
        scrapers=r"C:\proj\scrapers.yaml", db=r"C:\proj\jobs.db",
        log_path=r"C:\proj\logs\schedule.log",
        bat_dir=tmp_path,
    )

    # The task command points at the bat (well under the 261-char /TR limit).
    tr = called[0][called[0].index("/TR") + 1]
    assert "schedule-daily.bat" in tr
    assert len(tr) < 261
    assert all("companies.yaml" not in arg for arg in called[0])
    # The bat itself carries the absolute paths.
    bat = tmp_path / scheduler.TASK_BAT
    text = bat.read_text(encoding="utf-8")
    assert "cd /d" in text
    assert "companies.yaml" in text and "feeds.yaml" in text
    assert "scrapers.yaml" in text and "jobs.db" in text
    assert "\r\r\n" not in text


def test_install_windows_task_reports_schtasks_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler, "sys", type("S", (), {"platform": "win32",
                                                          "executable": "x"})())
    monkeypatch.setattr(scheduler.shutil, "which", lambda _: "schtasks")
    monkeypatch.setattr(scheduler.subprocess, "run",
                        lambda *a, **k: _Proc(returncode=1, stderr="boom"))
    with pytest.raises(RuntimeError, match="schtasks failed"):
        scheduler.install_windows_task(scheduler.time(9, 0), log_path="log",
                                       bat_dir=tmp_path)


def test_remove_windows_task_non_windows_raises(monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "sys", type("S", (), {"platform": "linux"})())
    with pytest.raises(RuntimeError, match="only available on Windows"):
        scheduler.remove_windows_task()
