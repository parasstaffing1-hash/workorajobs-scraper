#!/usr/bin/env python3
"""Production Job API Server — FastAPI with auth, rate limiting, PostgreSQL.

Endpoints:
  GET  /                    — Dashboard HTML
  GET  /api/health          — Health check (no auth)
  GET  /api/stats           — Database statistics
  GET  /api/jobs            — Search jobs with filters
  GET  /api/sources         — List all sources
  POST /api/webhook/n8n     — n8n daily trigger
  POST /api/keys/generate   — Generate new API key (admin)
  POST /api/keys/revoke     — Revoke API key (admin)
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── Import production modules ──────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from jobcollector.database import get_db
from jobcollector.middleware import (
    AuthRateMiddleware, REQUIRE_API_KEY,
    create_api_key, revoke_api_key, ADMIN_API_KEY,
)

# ── App Setup ──────────────────────────────────────────────────
app = FastAPI(
    title="Job Scraper API",
    version="3.0",
    description="Production job aggregation API with 1M+ jobs",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(AuthRateMiddleware)


# ── Health (no auth) ───────────────────────────────────────────
@app.get("/api/health")
def health():
    db = get_db()
    try:
        total = db.count()
        fresh = db.count_fresh(7)
        return {
            "status": "ok",
            "total_jobs": total,
            "fresh_7d": fresh,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Stats ──────────────────────────────────────────────────────
@app.get("/api/stats")
def stats():
    db = get_db()
    return db.get_stats()


# ── Sources ────────────────────────────────────────────────────
@app.get("/api/sources")
def sources():
    db = get_db()
    return {"sources": db.get_sources()}


# ── Jobs Search ────────────────────────────────────────────────
@app.get("/api/jobs")
def jobs(
    q: str = Query(None, description="Search title, company, description"),
    keyword: str = Query(None, description="Search title only"),
    company: str = Query(None, description="Filter by company"),
    location: str = Query(None, description="Filter by location"),
    source: str = Query(None, description="Filter by source"),
    source_kind: str = Query(None, description="Filter by source_kind"),
    days: int = Query(7, description="Jobs from last N days"),
    limit: int = Query(100, description="Max results (1-1000)"),
    offset: int = Query(0, description="Pagination offset"),
    sort: str = Query("newest", description="Sort: newest, oldest"),
):
    limit = min(max(limit, 1), 1000)
    db = get_db()

    search_term = q or keyword
    job_list, total = db.fetch_jobs(
        keyword=search_term, company=company, location=location,
        source=source, source_kind=source_kind, days=days,
        limit=limit, offset=offset, sort=sort,
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total,
        "jobs": job_list,
    }


# ── n8n Webhook ────────────────────────────────────────────────
@app.post("/api/webhook/n8n")
@app.get("/api/webhook/n8n")
def n8n_webhook(
    keyword: str = Query(None),
    location: str = Query(None),
    days: int = Query(1),
    limit: int = Query(500),
):
    limit = min(max(limit, 1), 5000)
    db = get_db()

    job_list, _ = db.fetch_jobs(
        keyword=keyword, location=location, days=days, limit=limit
    )

    total = db.count()
    fresh = db.count_fresh(days)

    return {
        "status": "ok",
        "trigger": "n8n_daily",
        "days": days,
        "keyword": keyword,
        "location": location,
        "total_db": total,
        "fresh_count": fresh,
        "count": len(job_list),
        "jobs": job_list,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Admin: API Key Management ──────────────────────────────────
@app.post("/api/keys/generate")
def api_keys_generate(
    request: Request,
    name: str = Query(""),
    tier: str = Query("free"),
    rate_limit: int = Query(60),
    x_admin_key: str = Header(None),
):
    """Generate a new API key. Requires admin key."""
    admin_key = x_admin_key or request.query_params.get("admin_key", "")
    if not REQUIRE_API_KEY or admin_key != ADMIN_API_KEY:
        raise HTTPException(403, "Admin key required")
    key = create_api_key(name=name, tier=tier, rate_limit=rate_limit)
    return {"api_key": key, "name": name, "tier": tier, "rate_limit": rate_limit}


@app.post("/api/keys/revoke")
def api_keys_revoke(
    request: Request,
    key: str = Query(...),
    x_admin_key: str = Header(None),
):
    """Revoke an API key. Requires admin key."""
    admin_key = x_admin_key or request.query_params.get("admin_key", "")
    if not REQUIRE_API_KEY or admin_key != ADMIN_API_KEY:
        raise HTTPException(403, "Admin key required")
    revoked = revoke_api_key(key)
    return {"revoked": revoked, "key": key[:8] + "..."}


# ── Dashboard HTML ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD_HTML


# ── Run ────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"[API] Starting production server on port {port}")
    print(f"[API] Auth: {'REQUIRED' if REQUIRE_API_KEY else 'disabled (set REQUIRE_API_KEY=true to enable)'}")
    print(f"[API] Rate limit: {os.environ.get('RATE_LIMIT_RPM', '120')} req/min")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


# ── Dashboard HTML (embedded) ──────────────────────────────────
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Scraper — Production Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:linear-gradient(135deg,#1e293b,#0f172a);padding:24px 32px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:24px;font-weight:700;background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header p{color:#94a3b8;margin-top:4px;font-size:14px}
.header .status{display:flex;gap:12px;align-items:center}
.dot{width:10px;height:10px;border-radius:50%;animation:pulse 2s infinite}
.dot.green{background:#4ade80}
.dot.yellow{background:#fbbf24}
.dot.red{background:#f87171}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
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
.source-bar .count{font-size:13px;color:#94a3b8;width:80px}
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
.api-box{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px 32px;margin:0 32px 24px}
.api-box h3{color:#a78bfa;margin-bottom:12px;font-size:14px}
.api-box code{background:#0f172a;padding:8px 12px;border-radius:6px;display:block;font-size:12px;color:#94a3b8;word-break:break-all;margin-bottom:8px}
.api-box p{color:#64748b;font-size:12px}
.refresh-bar{display:flex;justify-content:space-between;align-items:center;padding:0 32px 16px}
.refresh-bar .count{color:#64748b;font-size:12px}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🚀 Job Scraper — Production</h1>
    <p>Real-time view of <span id="total-header">0</span> jobs • Auto-refreshes every 15s</p>
  </div>
  <div class="status">
    <div class="dot green" id="status-dot"></div>
    <span id="status-text" style="font-size:13px;color:#94a3b8">Connected</span>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="label">Total Jobs</div><div class="value" id="total">--</div><div class="sub" id="fresh"></div></div>
  <div class="card green"><div class="label">Fresh (7 days)</div><div class="value" id="fresh-val">--</div><div class="sub" id="fresh-pct"></div></div>
  <div class="card purple"><div class="label">Companies</div><div class="value" id="comp">--</div><div class="sub" id="comp-url"></div></div>
  <div class="card orange"><div class="label">Sources</div><div class="value" id="src-count">--</div><div class="sub" id="src-kind"></div></div>
</div>

<div class="section"><h2>📊 Top Sources</h2><div id="sources"></div></div>

<div class="search-bar">
  <input type="text" id="search" placeholder="Search jobs... (e.g. software engineer, remote, Google)">
  <button onclick="searchJobs()">Search</button>
</div>

<div class="api-box">
  <h3>🔗 API Endpoints</h3>
  <code>GET /api/jobs?q=software+engineer&amp;location=Remote&amp;days=1&amp;limit=100</code>
  <code>GET /api/webhook/n8n?days=1&amp;keyword=python&amp;limit=500</code>
  <code>GET /api/stats</code>
  <code>GET /api/sources</code>
  <p>Set <code style="padding:2px 6px;background:#1e293b;border-radius:4px">X-API-Key</code> header for authenticated access</p>
</div>

<div class="section"><h2>🔍 Recent Jobs</h2>
<div class="refresh-bar"><span class="count" id="result-count"></span></div>
<table class="jobs-table">
<thead><tr><th>Title</th><th>Company</th><th>Location</th><th>Source</th><th>Date</th></tr></thead>
<tbody id="jobs-body"><tr><td colspan="5" style="text-align:center;color:#64748b">Loading...</td></tr></tbody>
</table>
</div>

<script>
async function loadStats(){
  try{
    const r=await fetch('/api/stats');const d=await r.json();
    document.getElementById('total').textContent=d.total_jobs.toLocaleString();
    document.getElementById('total-header').textContent=d.total_jobs.toLocaleString();
    document.getElementById('fresh-val').textContent=(d.daily_last_7d?Object.values(d.daily_last_7d).reduce((a,b)=>a+b,0):0).toLocaleString();
    const pct=((d.total_jobs/1000000)*100).toFixed(1);
    document.getElementById('fresh-pct').textContent=pct+'% of 1M target';
    document.getElementById('comp').textContent=d.unique_companies.toLocaleString();
    document.getElementById('comp-url').textContent=d.unique_urls.toLocaleString()+' unique URLs';
    document.getElementById('src-count').textContent=Object.keys(d.source_kinds).length;
    document.getElementById('src-kind').textContent=Object.entries(d.source_kinds).slice(0,3).map(([k,v])=>k+':'+v.toLocaleString()).join(', ');
    const max=Math.max(...Object.values(d.top_sources));
    let html='';
    for(const[k,v]of Object.entries(d.top_sources).slice(0,15)){
      const w=Math.max(2,(v/max)*100);
      html+=`<div class="source-bar"><div class="name">${k}</div><div class="bar" style="width:${w}%"></div><div class="count">${v.toLocaleString()}</div></div>`;
    }
    document.getElementById('sources').innerHTML=html;
    document.getElementById('status-dot').className='dot green';
    document.getElementById('status-text').textContent='Connected';
  }catch(e){
    document.getElementById('status-dot').className='dot red';
    document.getElementById('status-text').textContent='Disconnected';
  }
}

async function loadJobs(q){
  try{
    let url='/api/jobs?days=1&limit=50&sort=newest';
    if(q)url='/api/jobs?q='+encodeURIComponent(q)+'&limit=50';
    const r=await fetch(url);const d=await r.json();
    document.getElementById('result-count').textContent=d.total+' results';
    let html='';
    for(const j of d.jobs){
      const src=j.source_kind||'';
      const badge=src==='ats'?'badge-green':src==='web'?'badge-blue':src==='fast_http'?'badge-orange':'badge-purple';
      html+=`<tr><td><a href="${j.url}" target="_blank">${j.title}</a></td><td>${j.company||'-'}</td><td>${j.location||'-'}</td><td><span class="badge ${badge}">${j.source||'-'}</span></td><td>${j.first_seen_at?new Date(j.first_seen_at).toLocaleDateString():'-'}</td></tr>`;
    }
    document.getElementById('jobs-body').innerHTML=html||'<tr><td colspan="5" style="text-align:center;color:#64748b">No jobs found</td></tr>';
  }catch(e){console.error(e)}
}

function searchJobs(){loadJobs(document.getElementById('search').value)}
document.getElementById('search').addEventListener('keypress',e=>{if(e.key==='Enter')searchJobs()});
loadStats();loadJobs();
setInterval(()=>{loadStats();loadJobs()},15000);
</script>
</body>
</html>"""
