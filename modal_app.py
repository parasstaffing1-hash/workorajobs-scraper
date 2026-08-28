from __future__ import annotations

import os
from datetime import datetime, timezone

import modal

APP_NAME = "workorajobs-scraper-24x7"
STATE_NAME = "workorajobs-scraper-state"
RUNTIME_SECRET_NAME = "workorajobs-runtime"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "httpx>=0.27,<1",
        "boto3>=1.34,<2",
        "fastapi[standard]>=0.115,<1",
        "python-jobspy>=1.1,<2",
        "pandas>=2.2,<3",
    )
    .add_local_python_source("scripts")
    .add_local_file(
        "data/companies_10k.json",
        remote_path="/root/data/companies_10k.json",
    )
)

app = modal.App(APP_NAME)
state = modal.Dict.from_name(STATE_NAME, create_if_missing=True)
runtime_secret = modal.Secret.from_name(RUNTIME_SECRET_NAME)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.function(
    image=image,
    secrets=[runtime_secret],
    cpu=0.25,
    memory=2048,
    timeout=540,
    retries=2,
    max_containers=1,
    schedule=modal.Cron("*/15 * * * *"),
)
def ats_cycle() -> dict:
    """Continuously refresh a slice of the 10K-company ATS universe."""
    from scripts.modal_worker import run_ats_shard, send_heartbeat

    cursor = int(state.get("ats_cursor", 0) or 0)
    batch_size = int(os.getenv("ATS_BATCH_SIZE", "110"))
    result = run_ats_shard(start=cursor, batch_size=batch_size)

    state["ats_cursor"] = result["next_cursor"]
    state["ats_last_ok"] = _utcnow()
    state["ats_last_result"] = result
    state["last_ok"] = _utcnow()
    send_heartbeat(os.getenv("BETTERSTACK_ATS_HEARTBEAT_URL", ""))

    return result


@app.function(
    image=image,
    secrets=[runtime_secret],
    cpu=0.5,
    memory=3072,
    timeout=1500,
    retries=2,
    max_containers=1,
    schedule=modal.Cron("17 */2 * * *"),
)
def jobboard_cycle() -> dict:
    """Run JobSpy in an isolated container so it cannot OOM the ATS worker."""
    from scripts.modal_worker import run_jobboard_shard, send_heartbeat

    cursor = int(state.get("jobboard_cursor", 0) or 0)

    result = run_jobboard_shard(
        query_start=cursor,
        query_count=int(os.getenv("JOBBOARD_QUERIES_PER_RUN", "6")),
    )

    state["jobboard_cursor"] = result["next_cursor"]
    state["jobboard_last_ok"] = _utcnow()
    state["jobboard_last_result"] = result
    state["last_ok"] = _utcnow()
    send_heartbeat(os.getenv("BETTERSTACK_JOBBOARD_HEARTBEAT_URL", ""))

    return result


@app.function(
    image=image,
    cpu=0.125,
    memory=128,
    timeout=30,
)
@modal.fastapi_endpoint()
def health() -> dict:
    """Small public endpoint for Better Stack or a status dashboard."""
    now = _utcnow()
    return {
        "ok": True,
        "service": APP_NAME,
        "time": now,
        "ats_cursor": state.get("ats_cursor", 0),
        "ats_last_ok": state.get("ats_last_ok"),
        "jobboard_last_ok": state.get("jobboard_last_ok"),
        "last_ok": state.get("last_ok"),
    }


@app.local_entrypoint()
def run_once(source: str = "ats") -> None:
    """Manual smoke test: modal run modal_app.py --source ats|jobboard."""
    if source == "jobboard":
        print(jobboard_cycle.remote())
    else:
        print(ats_cycle.remote())
