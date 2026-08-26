#!/usr/bin/env python3
"""Job API Server — FastAPI backend for n8n integration and 24/7 hosting.

Endpoints:
  GET  /api/jobs          — Fetch jobs with filters (keyword, location, date, source)
  GET  /api/stats         — Database statistics
  GET  /api/health        — Health check
  GET  /api/sources       — List all sources and counts
  POST /api/webhook/n8n   — n8n daily trigger endpoint
  GET  /                  — Dashboard HTML page
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"

# ── Auth & Rate Limiting ─────────────────────────────────────
REQUIRE_API_KEY = os.environ.get("REQUIRE_API_KEY", "false").lower() == "true"
API_KEYS = set(k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip())
RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "120"))
_rate_buckets: dict[str, list[float]] = {}
_rate_lock = threading.Lock()

def _check_rate_limit(key: str) -> bool:
    now = time.time()
    window = 60.0
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, [])
        bucket[:] = [t for t in bucket if now - t < window]
        if len(bucket) >= RATE_LIMIT_RPM:
            return False
        bucket.append(now)
        return True

app = FastAPI(title="Job Scraper API", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def auth_rate_middleware(request: Request, call_next):
    # Skip auth for health check and dashboard
    if request.url.path in ("/api/health", "/", ""):
        return await call_next(request)
    # Rate limit
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return JSONResponse({"error": "Rate limit exceeded", "retry_after": 60}, status_code=429)
    # API key auth
    if REQUIRE_API_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if api_key not in API_KEYS:
            return JSONResponse({"error": "Invalid or missing X-API-Key header"}, status_code=401)
    return await call_next(request)

# ── DB helper ─────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

# ── Health ────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    # Always accessible, no auth
    try:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        return {"status": "ok", "total_jobs": total, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ── Stats ─────────────────────────────────────────────────────
@app.get("/api/stats")
def stats():
    # Requires auth if enabled
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        
        # By source_kind
        source_kinds = {}
        for row in conn.execute("SELECT source_kind, COUNT(*) as cnt FROM jobs GROUP BY source_kind ORDER BY cnt DESC"):
            source_kinds[row[0] or "unknown"] = row[1]
        
        # By top sources
        top_sources = {}
        for row in conn.execute("SELECT source, COUNT(*) as cnt FROM jobs GROUP BY source ORDER BY cnt DESC LIMIT 20"):
            top_sources[row[0]] = row[1]
        
        # Unique companies
        companies = conn.execute("SELECT COUNT(DISTINCT LOWER(TRIM(company))) FROM jobs WHERE company != ''").fetchone()[0]
        
        # Unique URLs
        urls = conn.execute("SELECT COUNT(DISTINCT url) FROM jobs").fetchone()[0]
        
        # Jobs by day (last 7 days)
        daily = {}
        for row in conn.execute("""
            SELECT date(first_seen_at) as day, COUNT(*) as cnt 
            FROM jobs 
            WHERE first_seen_at >= date('now', '-7 days')
            GROUP BY day ORDER BY day
        """):
            daily[row[0]] = row[1]
        
        return {
            "total_jobs": total,
            "unique_companies": companies,
            "unique_urls": urls,
            "source_kinds": source_kinds,
            "top_sources": top_sources,
            "daily_last_7d": daily,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()

# ── Sources ───────────────────────────────────────────────────
@app.get("/api/sources")
def sources():
    # Requires auth if enabled
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT source, source_kind, COUNT(*) as cnt FROM jobs GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        return {"sources": [{"source": r[0], "kind": r[1], "count": r[2]} for r in rows]}
    finally:
        conn.close()

# ── Jobs ──────────────────────────────────────────────────────
@app.get("/api/jobs")
def jobs(
    q: str = Query(None, description="Search in title, company, description"),
    keyword: str = Query(None, description="Search in title only"),
    company: str = Query(None, description="Filter by company name"),
    location: str = Query(None, description="Filter by location"),
    source: str = Query(None, description="Filter by source (e.g. greenhouse:stripe)"),
    source_kind: str = Query(None, description="Filter by source_kind (e.g. ats, web, fast_http)"),
    days: int = Query(7, description="Jobs from last N days"),
    limit: int = Query(100, description="Max results (1-1000)"),
    offset: int = Query(0, description="Pagination offset"),
    sort: str = Query("newest", description="Sort: newest, oldest"),
):
    """Fetch jobs with filters. Returns JSON array of job objects."""
    limit = min(max(limit, 1), 1000)
    
    conn = get_db()
    try:
        conditions = []
        params = []
        
        if q:
            conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])
        
        if keyword:
            conditions.append("title LIKE ?")
            params.append(f"%{keyword}%")
        
        if company:
            conditions.append("LOWER(company) LIKE ?")
            params.append(f"%{company.lower()}%")
        
        if location:
            conditions.append("LOWER(location) LIKE ?")
            params.append(f"%{location.lower()}%")
        
        if source:
            conditions.append("source = ?")
            params.append(source)
        
        if source_kind:
            conditions.append("source_kind = ?")
            params.append(source_kind)
        
        if days and days > 0:
            conditions.append("first_seen_at >= ?")
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            params.append(cutoff)
        
        where = " AND ".join(conditions) if conditions else "1=1"
        order = "first_seen_at DESC" if sort == "newest" else "first_seen_at ASC"
        
        # Get total count
        count_sql = f"SELECT COUNT(*) FROM jobs WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()[0]
        
        # Get results
        sql = f"""
            SELECT title, company, location, url, source, source_kind, 
                   posted_at, salary, description, first_seen_at
            FROM jobs WHERE {where} 
            ORDER BY {order} 
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        
        result = []
        for r in rows:
            result.append({
                "title": r[0],
                "company": r[1],
                "location": r[2],
                "url": r[3],
                "source": r[4],
                "source_kind": r[5],
                "posted_at": r[6],
                "salary": r[7],
                "description": r[8][:300] if r[8] else "",
                "first_seen_at": r[9],
            })
        
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
            "jobs": result,
        }
    finally:
        conn.close()

# ── n8n Webhook ───────────────────────────────────────────────
@app.post("/api/webhook/n8n")
@app.get("/api/webhook/n8n")
def n8n_webhook(
    keyword: str = Query(None, description="Search keyword"),
    location: str = Query(None, description="Location filter"),
    days: int = Query(1, description="Days to look back"),
    limit: int = Query(500, description="Max results"),
):
    """n8n daily trigger endpoint. Returns jobs posted in last N days.
    
    n8n workflow:
    1. Cron trigger at 9 AM daily
    2. HTTP Request → GET /api/webhook/n8n?days=1&limit=500
    3. Process/send results
    """
    conn = get_db()
    try:
        conditions = ["first_seen_at >= ?"]
        params = [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()]
        
        if keyword:
            conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        
        if location:
            conditions.append("LOWER(location) LIKE ?")
            params.append(f"%{location.lower()}%")
        
        where = " AND ".join(conditions)
        limit = min(max(limit, 1), 5000)
        
        sql = f"""
            SELECT title, company, location, url, source, posted_at, salary, description
            FROM jobs WHERE {where}
            ORDER BY first_seen_at DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        
        jobs = []
        for r in rows:
            jobs.append({
                "title": r[0],
                "company": r[1],
                "location": r[2],
                "url": r[3],
                "source": r[4],
                "posted_at": r[5],
                "salary": r[6],
                "description": (r[7] or "")[:300],
            })
        
        # Get stats
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        
        return {
            "status": "ok",
            "trigger": "n8n_daily",
            "days": days,
            "keyword": keyword,
            "location": location,
            "total_db": total,
            "count": len(jobs),
            "jobs": jobs,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()

# ── Dashboard HTML ────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard():
    # Serve lead scraper dashboard if available
    lead_path = ROOT / "lead_scraper.html"
    if lead_path.exists():
        from fastapi.responses import FileResponse as FR
        return FR(str(lead_path), media_type="text/html")
    return """<!DOCTYPE html>
<html lang="en">
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Scraper Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:linear-gradient(135deg,#1e293b,#0f172a);padding:24px 32px;border-bottom:1px solid #334155}
.header h1{font-size:24px;font-weight:700;background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header p{color:#94a3b8;margin-top:4px;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;padding:24px 32px}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;transition:all .2s}
.card:hover{border-color:#475569;transform:translateY(-2px)}
.card .label{color:#94a3b8;font-size:13px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.card .value{font-size:32px;font-weight:700;color:#38bdf8}
.card .sub{color:#64748b;font-size:13px;margin-top:4px}
.card.green .value{color:#4ade80}
.card.purple .value{color:#a78bfa}
.card.orange .value{color:#fb923c}
.card.pink .value{color:#f472b6}
.section{padding:0 32px 24px}
.section h2{font-size:18px;margin-bottom:16px;color:#f8fafc}
.source-bar{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.source-bar .name{width:200px;font-size:13px;color:#cbd5e1;text-align:right;flex-shrink:0}
.source-bar .bar{height:24px;border-radius:4px;background:linear-gradient(90deg,#38bdf8,#818cf8);min-width:2px;transition:width .5s}
.source-bar .count{font-size:13px;color:#94a3b8;width:60px}
.jobs-table{width:100%;border-collapse:collapse;font-size:13px}
.jobs-table th{text-align:left;padding:10px 12px;border-bottom:2px solid #334155;color:#94a3b8;font-weight:600}
.jobs-table td{padding:8px 12px;border-bottom:1px solid #1e293b}
.jobs-table tr:hover td{background:#1e293b}
.jobs-table a{color:#38bdf8;text-decoration:none}
.jobs-table a:hover{text-decoration:underline}
.badge{display:inline-block;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:600}
.badge-green{background:#065f46;color:#6ee7b7}
.badge-blue{background:#1e3a5f;color:#93c5fd}
.badge-purple{background:#3b0764;color:#d8b4fe}
.badge-orange{background:#7c2d12;color:#fdba74}
.search-bar{display:flex;gap:12px;padding:0 32px 16px}
.search-bar input{flex:1;padding:10px 16px;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;font-size:14px;outline:none}
.search-bar input:focus{border-color:#38bdf8}
.search-bar button{padding:10px 24px;border-radius:8px;border:none;background:#38bdf8;color:#0f172a;font-weight:600;cursor:pointer;font-size:14px}
.search-bar button:hover{background:#7dd3fc}
.n8n-box{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px 32px;margin:0 32px 24px}
.n8n-box h3{color:#a78bfa;margin-bottom:8px;font-size:14px}
.n8n-box code{background:#0f172a;padding:8px 12px;border-radius:6px;display:block;font-size:12px;color:#94a3b8;word-break:break-all}
.auto-refresh{color:#64748b;font-size:12px;text-align:center;padding:16px}
</style>
</head>
<body>
<div class="header">
<h1>🚀 Job Scraper Dashboard</h1>
<p>Real-time view of scraped jobs • Auto-refreshes every 30s</p>
</div>

<div class="grid" id="stats-grid">
<div class="card"><div class="label">Total Jobs</div><div class="value" id="total">--</div><div class="sub" id="companies"></div></div>
<div class="card green"><div class="label">Gap to 1M</div><div class="value" id="gap">--</div><div class="sub" id="pct"></div></div>
<div class="card purple"><div class="label">Unique Companies</div><div class="value" id="comp">--</div></div>
<div class="card orange"><div class="label">Sources Active</div><div class="value" id="src-count">--</div><div class="sub" id="src-kind"></div></div>
</div>

<div class="section">
<h2>📊 Jobs by Source</h2>
<div id="sources"></div>
</div>

<div class="search-bar">
<input type="text" id="search" placeholder="Search jobs... (e.g. software engineer, remote, Google)">
<button onclick="searchJobs()">Search</button>
</div>

<div class="n8n-box">
<h3>🔗 n8n Integration</h3>
<code>GET /api/webhook/n8n?days=1&amp;keyword=software+engineer&amp;limit=500</code>
<p style="color:#64748b;font-size:12px;margin-top:8px">Set this as HTTP Request URL in your n8n workflow • Runs daily at 9 AM via Cron trigger</p>
</div>

<div class="section">
<h2>🔍 Recent Jobs</h2>
<table class="jobs-table">
<thead><tr><th>Title</th><th>Company</th><th>Location</th><th>Source</th><th>Posted</th></tr></thead>
<tbody id="jobs-body"><tr><td colspan="5" style="text-align:center;color:#64748b">Loading...</td></tr></tbody>
</table>
</div>

<div class="auto-refresh">Auto-refreshing every 30 seconds</div>

<script>
async function loadStats(){
  try{
    const r=await fetch('/api/stats');const d=await r.json();
    document.getElementById('total').textContent=d.total_jobs.toLocaleString();
    document.getElementById('gap').textContent=Math.max(0,1000000-d.total_jobs).toLocaleString();
    document.getElementById('pct').textContent=(d.total_jobs/10000).toFixed(1)+'% of 1M target';
    document.getElementById('comp').textContent=d.unique_companies.toLocaleString();
    document.getElementById('companies').textContent=d.unique_urls.toLocaleString()+' unique URLs';
    document.getElementById('src-count').textContent=Object.keys(d.source_kinds).length;
    document.getElementById('src-kind').textContent=Object.entries(d.source_kinds).slice(0,3).map(([k,v])=>k+':'+v).join(', ');
    const max=Math.max(...Object.values(d.top_sources));
    let html='';
    for(const[k,v]of Object.entries(d.top_sources).slice(0,12)){
      const w=Math.max(2,(v/max)*100);
      html+=`<div class="source-bar"><div class="name">${k}</div><div class="bar" style="width:${w}%"></div><div class="count">${v.toLocaleString()}</div></div>`;
    }
    document.getElementById('sources').innerHTML=html;
  }catch(e){console.error(e)}
}

async function loadJobs(){
  try{
    const r=await fetch('/api/jobs?days=1&limit=30&sort=newest');const d=await r.json();
    let html='';
    for(const j of d.jobs){
      const src=j.source_kind||'';
      const badge=src==='ats'?'badge-green':src==='web'?'badge-blue':src==='fast_http'?'badge-orange':'badge-purple';
      html+=`<tr>
        <td><a href="${j.url}" target="_blank">${j.title}</a></td>
        <td>${j.company||'-'}</td>
        <td>${j.location||'-'}</td>
        <td><span class="badge ${badge}">${j.source||'-'}</span></td>
        <td>${j.first_seen_at?new Date(j.first_seen_at).toLocaleDateString():'-'}</td>
      </tr>`;
    }
    document.getElementById('jobs-body').innerHTML=html||'<tr><td colspan="5" style="text-align:center;color:#64748b">No jobs found</td></tr>';
  }catch(e){console.error(e)}
}

async function searchJobs(){
  const q=document.getElementById('search').value;
  if(!q)return loadJobs();
  try{
    const r=await fetch('/api/jobs?q='+encodeURIComponent(q)+'&limit=30');const d=await r.json();
    let html='';
    for(const j of d.jobs){
      html+=`<tr>
        <td><a href="${j.url}" target="_blank">${j.title}</a></td>
        <td>${j.company||'-'}</td>
        <td>${j.location||'-'}</td>
        <td><span class="badge badge-blue">${j.source||'-'}</span></td>
        <td>${j.first_seen_at?new Date(j.first_seen_at).toLocaleDateString():'-'}</td>
      </tr>`;
    }
    document.getElementById('jobs-body').innerHTML=html||'<tr><td colspan="5">No results</td></tr>';
  }catch(e){console.error(e)}
}

document.getElementById('search').addEventListener('keypress',e=>{if(e.key==='Enter')searchJobs()});

loadStats();loadJobs();
setInterval(()=>{loadStats();loadJobs()},30000);
</script>
</body>
</html>"""

# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting Job API Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
