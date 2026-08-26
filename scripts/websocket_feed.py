#!/usr/bin/env python3
"""WebSocket Live Feed — Push new jobs to dashboard in real-time.

Integrates with the FastAPI API server to provide WebSocket connections
for live job notifications as they're scraped.

Usage:
    python -m scripts.websocket_feed --port 8765
"""
from __future__ import annotations
import asyncio, json, os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "websocket_feed.log"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# WebSocket server
connected_clients = set()
last_check_time = time.time()


async def broadcast(message):
    """Broadcast message to all connected clients."""
    if not connected_clients:
        return
    dead = set()
    for client in connected_clients:
        try:
            await client.send(json.dumps(message))
        except:
            dead.add(client)
    connected_clients -= dead


async def monitor_jobs():
    """Monitor database for new jobs and broadcast to clients."""
    global last_check_time

    last_count = get_job_count()
    log(f"Job monitor started. Current jobs: {last_count}")

    while True:
        try:
            await asyncio.sleep(5)  # Check every 5 seconds

            current_count = get_job_count()
            if current_count > last_count:
                new_count = current_count - last_count
                # Get recent jobs
                recent = get_recent_jobs(new_count)

                await broadcast({
                    "type": "new_jobs",
                    "count": len(recent),
                    "total": current_count,
                    "jobs": recent[:50],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

                log(f"Broadcast {len(recent)} new jobs (total: {current_count})")

            # Broadcast stats update
            stats = get_stats()
            await broadcast({
                "type": "stats_update",
                "data": stats,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            last_count = current_count

        except Exception as e:
            log(f"Monitor error: {e}")
            await asyncio.sleep(5)


def get_job_count():
    try:
        conn = sqlite3.connect(str(DB), timeout=5)
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
        conn.close()
        return count
    except:
        return 0


def get_recent_jobs(count=10):
    try:
        conn = sqlite3.connect(str(DB), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT title, company, location, url, source, posted_at "
            "FROM jobs WHERE is_active = 1 "
            "ORDER BY first_seen_at DESC LIMIT ?",
            (count,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []


def get_stats():
    try:
        conn = sqlite3.connect(str(DB), timeout=5)
        total = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
        companies = conn.execute(
            "SELECT COUNT(DISTINCT LOWER(TRIM(company))) FROM jobs WHERE company != '' AND is_active = 1"
        ).fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE is_active = 1 AND first_seen_at >= date('now')"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "companies": companies, "today": today}
    except:
        return {"total": 0, "companies": 0, "today": 0}


async def websocket_handler(websocket):
    """Handle WebSocket connections."""
    connected_clients.add(websocket)
    log(f"Client connected. Total: {len(connected_clients)}")

    try:
        # Send initial data
        stats = get_stats()
        await websocket.send(json.dumps({
            "type": "initial",
            "data": stats,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }))

        # Listen for messages
        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get("type") == "get_jobs":
                    jobs = get_recent_jobs(data.get("count", 20))
                    await websocket.send(json.dumps({
                        "type": "jobs",
                        "data": jobs
                    }))
                elif data.get("type") == "get_stats":
                    stats = get_stats()
                    await websocket.send(json.dumps({
                        "type": "stats",
                        "data": stats
                    }))
            except:
                pass
    except Exception as e:
        log(f"Client error: {e}")
    finally:
        connected_clients.discard(websocket)
        log(f"Client disconnected. Total: {len(connected_clients)}")


async def main(port=8765):
    """Start WebSocket server."""
    try:
        import websockets
    except ImportError:
        log("websockets not installed. pip install websockets")
        return

    log(f"Starting WebSocket server on port {port}")

    # Start job monitor
    asyncio.create_task(monitor_jobs())

    # Start WebSocket server
    async with websockets.serve(websocket_handler, "0.0.0.0", port):
        log(f"WebSocket server running on ws://0.0.0.0:{port}")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    asyncio.run(main(port))
