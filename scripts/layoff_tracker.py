#!/usr/bin/env python3
"""Layoff Tracker — Track layoffs, generate social media posts, drive organic traffic.

Scrapes layoff data from multiple sources:
- layoffs.fyi (main tracker)
- TechCrunch layoff coverage
- News articles
- Google News

Generates viral social media posts about layoffs for organic traffic.

Usage:
    python -m scripts.layoff_tracker --scrape
    python -m scripts.layoff_tracker --social
    python -m scripts.layoff_tracker --analytics
    python -m scripts.layoff_tracker --dashboard
"""
from __future__ import annotations
import hashlib, json, os, re, sqlite3, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LAYOFF_DB = ROOT / "layoffs.db"
LOG = ROOT / "layoff_tracker.log"
POSTS_DIR = ROOT / "social_posts"


def get_db():
    """Get layoff database connection."""
    conn = sqlite3.connect(str(LAYOFF_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Initialize layoff database schema."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS layoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            industry TEXT,
            location TEXT,
            country TEXT DEFAULT 'US',
            employees_laid_off INTEGER,
            percentage_laid_off REAL,
            date TEXT,
            source TEXT,
            source_url TEXT,
            description TEXT,
            tech_stack TEXT,
            funding_stage TEXT,
            total_funding TEXT,
            company_size TEXT,
            department TEXT,
            severance_info TEXT,
            is_verified INTEGER DEFAULT 0,
            first_seen TEXT DEFAULT (datetime('now')),
            last_updated TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS layoff_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            url TEXT,
            last_scraped TEXT,
            total_entries INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS social_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            layoff_id INTEGER,
            platform TEXT,
            post_text TEXT,
            hashtags TEXT,
            posted_at TEXT,
            likes INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            FOREIGN KEY (layoff_id) REFERENCES layoffs(id)
        );

        CREATE TABLE IF NOT EXISTS company_layoff_history (
            company TEXT PRIMARY KEY,
            total_layoffs INTEGER DEFAULT 0,
            total_employees_affected INTEGER DEFAULT 0,
            first_layoff_date TEXT,
            last_layoff_date TEXT,
            layoff_count INTEGER DEFAULT 0,
            avg_percentage REAL DEFAULT 0,
            is_recurring INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'low'
        );

        CREATE INDEX IF NOT EXISTS idx_layoffs_company ON layoffs(company);
        CREATE INDEX IF NOT EXISTS idx_layoffs_date ON layoffs(date);
        CREATE INDEX IF NOT EXISTS idx_layoffs_industry ON layoffs(industry);
    """)
    conn.close()
    log("Layoff database initialized")


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_url(url):
    """Fetch URL content."""
    try:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""


# ── Layoff Data Scraping ─────────────────────────────────────
def scrape_layoffs_fyi():
    """Scrape layoffs.fyi for recent layoff data."""
    log("Scraping layoffs.fyi...")
    url = "https://layoffs.fyi/"
    html = fetch_url(url)
    if not html:
        log("Failed to fetch layoffs.fyi")
        return []

    layoffs = []

    # Extract layoff entries from the page
    # layoffs.fyi uses a spreadsheet-style format
    patterns = [
        r'"company":"([^"]+)".*?"layoff_count":(\d+).*?"date":"([^"]+)"',
        r'"Company":"([^"]+)".*?"# Laid Off":(\d+).*?"Date":"([^"]+)"',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches[:100]:
            company, count, date = match
            layoffs.append({
                "company": company,
                "employees_laid_off": int(count),
                "date": date,
                "source": "layoffs.fyi",
                "source_url": url,
            })

    # Also try to extract from table rows
    table_pattern = r'<tr[^>]*>(.*?)</tr>'
    rows = re.findall(table_pattern, html, re.DOTALL)
    for row in rows[:200]:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) >= 3:
            company = re.sub(r'<[^>]+>', '', cells[0]).strip()
            laid_off = re.sub(r'<[^>]+>', '', cells[1]).strip()
            date = re.sub(r'<[^>]+>', '', cells[2]).strip() if len(cells) > 2 else ""

            if company and laid_off:
                try:
                    layoffs.append({
                        "company": company,
                        "employees_laid_off": int(re.sub(r'[^0-9]', '', laid_off) or 0),
                        "date": date,
                        "source": "layoffs.fyi",
                        "source_url": url,
                    })
                except:
                    pass

    log(f"Found {len(layoffs)} entries from layoffs.fyi")
    return layoffs


def scrape_techcrunch_layoffs():
    """Scrape TechCrunch layoff coverage."""
    log("Scraping TechCrunch layoffs...")
    url = "https://techcrunch.com/tag/layoffs/"
    html = fetch_url(url)
    if not html:
        return []

    layoffs = []
    # Extract article links and headlines
    article_pattern = r'<h[23][^>]*><a[^>]*href="([^"]+)"[^>]*>([^<]+)</a></h[23]>'
    articles = re.findall(article_pattern, html, re.IGNORECASE)

    for url, title in articles[:50]:
        if any(kw in title.lower() for kw in ["layoff", "laid off", "layoffs", "cut", "slash"]):
            # Try to extract company name from title
            company_match = re.search(r'(\w+(?:\s+\w+)?)\s+(?:lay|cut|slash|layoff)', title, re.I)
            company = company_match.group(1) if company_match else title.split()[0]

            layoffs.append({
                "company": company,
                "description": title,
                "source": "techcrunch",
                "source_url": url,
            })

    log(f"Found {len(layoffs)} entries from TechCrunch")
    return layoffs


def scrape_google_news():
    """Scrape Google News for layoff stories."""
    log("Scraping Google News layoffs...")
    queries = [
        "tech layoffs today",
        "company layoffs 2024",
        "layoffs tech industry",
        "startup layoffs",
        "big tech layoffs",
    ]

    layoffs = []
    for query in queries[:3]:
        url = f"https://news.google.com/search?q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
        html = fetch_url(url)
        if not html:
            continue

        # Extract article titles
        title_pattern = r'"title":"([^"]*layo[^"]*)"'
        titles = re.findall(title_pattern, html, re.IGNORECASE)

        for title in titles[:20]:
            # Extract company name
            company_match = re.search(r'(\w+(?:\s+\w+){0,2})\s+(?:to|will|is|lays?|cut|slash)', title, re.I)
            company = company_match.group(1) if company_match else ""

            layoffs.append({
                "company": company,
                "description": title,
                "source": "google_news",
            })

        time.sleep(1)

    log(f"Found {len(layoffs)} entries from Google News")
    return layoffs


def scrape_all_layoffs():
    """Scrape all layoff sources and save to database."""
    all_layoffs = []

    # Scrape from multiple sources
    all_layoffs.extend(scrape_layoffs_fyi())
    all_layoffs.extend(scrape_techcrunch_layoffs())
    all_layoffs.extend(scrape_google_news())

    # Save to database
    conn = get_db()
    saved = 0

    for layoff in all_layoffs:
        company = layoff.get("company", "").strip()
        if not company or len(company) < 2:
            continue

        # Check for duplicates
        existing = conn.execute(
            "SELECT id FROM layoffs WHERE company = ? AND date = ?",
            (company, layoff.get("date", ""))
        ).fetchone()

        if not existing:
            try:
                conn.execute(
                    """INSERT INTO layoffs (company, industry, location, country,
                       employees_laid_off, percentage_laid_off, date, source,
                       source_url, description, tech_stack, funding_stage)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (company,
                     layoff.get("industry", ""),
                     layoff.get("location", ""),
                     layoff.get("country", "US"),
                     layoff.get("employees_laid_off", 0),
                     layoff.get("percentage_laid_off", 0),
                     layoff.get("date", ""),
                     layoff.get("source", ""),
                     layoff.get("source_url", ""),
                     layoff.get("description", ""),
                     layoff.get("tech_stack", ""),
                     layoff.get("funding_stage", ""))
                )
                saved += 1
            except Exception as e:
                log(f"Error saving: {e}")

    conn.commit()
    conn.close()

    log(f"Saved {saved} new layoff entries (total scraped: {len(all_layoffs)})")
    return saved


# ── Social Media Post Generator ──────────────────────────────
def generate_layoff_posts(platform="linkedin", limit=10):
    """Generate viral social media posts about layoffs."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Get recent layoffs
    layoffs = conn.execute(
        "SELECT * FROM layoffs ORDER BY employees_laid_off DESC LIMIT ?",
        (limit * 3,)
    ).fetchall()

    conn.close()

    posts = []
    for layoff in layoffs:
        layoff = dict(layoff)
        company = layoff.get("company", "Unknown")
        count = layoff.get("employees_laid_off", 0)
        date = layoff.get("date", "recently")
        industry = layoff.get("industry", "tech")

        if platform == "linkedin":
            post = generate_linkedin_post(layoff)
        elif platform == "twitter":
            post = generate_twitter_post(layoff)
        elif platform == "reddit":
            post = generate_reddit_post(layoff)
        else:
            post = generate_linkedin_post(layoff)

        posts.append(post)

    return posts


def generate_linkedin_post(layoff):
    """Generate a LinkedIn post about a layoff."""
    company = layoff.get("company", "Unknown")
    count = layoff.get("employees_laid_off", 0)
    date = layoff.get("date", "recently")
    industry = layoff.get("industry", "tech")
    description = layoff.get("description", "")

    # Viral hooks
    hooks = [
        f"🚨 BREAKING: {company} just laid off {count} employees" if count else f"🚨 BREAKING: {company} just announced layoffs",
        f"⚠️ {company} is cutting {count} jobs. Here's what you need to know." if count else f"⚠️ {company} layoffs announced. Here's what you need to know.",
        f"💔 Another {industry} giant cuts jobs: {company} lays off {count}+" if count else f"💔 {industry} layoffs continue: {company} announces cuts",
        f"🔥 {company} just joined the layoff wave. {count} employees affected." if count else f"🔥 {company} joins the growing list of companies laying off staff",
        f"📊 {company} Layoff Report: {count} jobs cut, {industry} sector hit hard" if count else f"📊 Breaking: {company} announces layoffs in {industry}",
    ]

    # Body templates
    bodies = [
        f"""Here's what we know so far:
        
📍 Company: {company}
👥 Employees affected: {count:,}+
📅 When: {date}
🏢 Industry: {industry}

{'📝 ' + description if description else ''}

This brings the total tech layoffs this year to over 100,000.

What does this mean for the job market?
→ More competition for remaining roles
→ Companies are prioritizing profitability over growth
→ Skills in AI/ML remain in high demand

If you're affected, remember:
✅ Update your LinkedIn profile
✅ Reach out to your network
✅ Focus on in-demand skills
✅ Don't panic — this is temporary

#Layoffs #{company.replace(' ', '')} #TechJobs #CareerAdvice #JobSearch""",

        f"""🚨 Just in: {company} layoffs

{count:,}+ employees are being laid off from {company} in what's being called one of the biggest {industry} layoffs this year.

Here's my take on what this means for the industry:

1️⃣ The growth-at-all-costs era is over
2️⃣ Companies are restructuring for AI
3️⃣ The talent market is shifting rapidly
4️⃣ Remote work policies are changing

For job seekers:
• Focus on AI/ML skills
• Build in-demand tech stacks
• Network actively
• Consider startups — they're hiring

What are your thoughts on this layoff wave?

#Layoffs #{company.replace(' ', '')} #TechIndustry #AI #CareerGrowth""",

        f"""The layoff wave continues. {company} just announced {count:,}+ job cuts.

This isn't just about one company — it's about a fundamental shift in how {industry} companies operate.

Key takeaways:
📊 Profitability > Growth
🤖 AI is reshaping teams
🌐 Remote work is being reevaluated
💡 Skills > Experience

If you're looking for a job right now:
1. Highlight AI/ML experience
2. Show quantifiable results
3. Network strategically
4. Consider contract/freelance work

The market will recover. The question is: will you be ready?

#Layoffs #TechJobs #{company.replace(' ', '')} #JobSearch #AI"""
    ]

    import random
    hook = random.choice(hooks)
    body = random.choice(bodies)

    return {
        "platform": "linkedin",
        "text": f"{hook}\n\n{body}",
        "hashtags": f"#Layoffs #{company.replace(' ', '')} #TechJobs #CareerAdvice",
        "company": company,
        "count": count,
    }


def generate_twitter_post(layoff):
    """Generate a Twitter/X post about a layoff."""
    company = layoff.get("company", "Unknown")
    count = layoff.get("employees_laid_off", 0)
    date = layoff.get("date", "recently")

    tweets = [
        f"🚨 BREAKING: {company} laying off {count}+ employees\n\nThe layoff wave continues...\n\n#Layoffs #{company.replace(' ', '')} #Tech",
        f"⚠️ {company} cuts {count} jobs as {industry} slowdown continues\n\nAffected employees: check your options\n\n#Layoffs #TechJobs",
        f"💔 {company} lays off {count}+ workers\n\n{date}\n\nTotal tech layoffs this year: 100K+\n\n#TechLayoffs",
        f"📊 {company} Layoff Report\n\n• {count} employees affected\n• Industry: Tech\n• {date}\n\nMore details coming soon.\n\n#Layoffs",
    ]

    import random
    return {
        "platform": "twitter",
        "text": random.choice(tweets),
        "hashtags": f"#Layoffs #{company.replace(' ', '')}",
        "company": company,
        "count": count,
    }


def generate_reddit_post(layoff):
    """Generate a Reddit post about a layoff."""
    company = layoff.get("company", "Unknown")
    count = layoff.get("employees_laid_off", 0)
    date = layoff.get("date", "recently")

    return {
        "platform": "reddit",
        "title": f"[Layoff] {company} laying off {count}+ employees ({date})" if count else f"[Layoff] {company} announces layoffs ({date})",
        "text": f"Just saw this news. {company} is laying off {'approximately ' + str(count) + ' employees' if count else 'an unspecified number of employees'}.\n\nThoughts?\n\nSource: layoffs.fyi",
        "subreddit": "cscareerquestions",
        "company": company,
        "count": count,
    }


def save_social_posts(posts):
    """Save generated posts to database."""
    conn = get_db()
    saved = 0

    for post in posts:
        try:
            conn.execute(
                """INSERT INTO social_posts (layoff_id, platform, post_text, hashtags)
                   VALUES (?, ?, ?, ?)""",
                (None, post.get("platform"), post.get("text"), post.get("hashtags"))
            )
            saved += 1
        except Exception as e:
            log(f"Error saving post: {e}")

    conn.commit()
    conn.close()
    return saved


def export_posts_to_files(platform="linkedin", limit=10):
    """Export posts to files for easy copy-paste."""
    POSTS_DIR.mkdir(exist_ok=True)

    posts = generate_layoff_posts(platform, limit)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = POSTS_DIR / f"{platform}_posts_{timestamp}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"{'='*60}\n")
        f.write(f"  {platform.upper()} POSTS - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"{'='*60}\n\n")

        for i, post in enumerate(posts, 1):
            f.write(f"--- Post {i} ---\n")
            f.write(f"Company: {post.get('company', 'N/A')}\n")
            f.write(f"Laid Off: {post.get('count', 'N/A')}\n")
            f.write(f"---\n")
            f.write(f"{post['text']}\n")
            f.write(f"\n{'='*60}\n\n")

    log(f"Exported {len(posts)} posts to {filename}")
    return str(filename)


# ── Analytics ────────────────────────────────────────────────
def get_layoff_analytics():
    """Get comprehensive layoff analytics."""
    conn = get_db()

    # Total layoffs
    total = conn.execute("SELECT COUNT(*) FROM layoffs").fetchone()[0]
    total_affected = conn.execute("SELECT COALESCE(SUM(employees_laid_off), 0) FROM layoffs").fetchone()[0]

    # By industry
    by_industry = conn.execute(
        "SELECT industry, COUNT(*) as cnt, SUM(employees_laid_off) as affected "
        "FROM layoffs WHERE industry != '' GROUP BY industry ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    # By month
    by_month = conn.execute(
        "SELECT strftime('%Y-%m', date) as month, COUNT(*) as cnt "
        "FROM layoffs WHERE date != '' GROUP BY month ORDER BY month DESC LIMIT 12"
    ).fetchall()

    # Top companies with most layoffs
    top_companies = conn.execute(
        "SELECT company, SUM(employees_laid_off) as total, COUNT(*) as times "
        "FROM layoffs GROUP BY company ORDER BY total DESC LIMIT 20"
    ).fetchall()

    # Recent layoffs (last 7 days)
    recent = conn.execute(
        "SELECT company, employees_laid_off, date, source "
        "FROM layoffs WHERE date >= date('now', '-7 days') "
        "ORDER BY employees_laid_off DESC LIMIT 20"
    ).fetchall()

    # Recurring layoff companies
    recurring = conn.execute(
        "SELECT company, COUNT(*) as times, SUM(employees_laid_off) as total "
        "FROM layoffs GROUP BY company HAVING times > 1 ORDER BY times DESC LIMIT 10"
    ).fetchall()

    # Layoff trends (month over month)
    trends = conn.execute(
        "SELECT strftime('%Y-%m', date) as month, COUNT(*) as cnt, "
        "SUM(employees_laid_off) as affected "
        "FROM layoffs WHERE date != '' "
        "GROUP BY month ORDER BY month DESC LIMIT 6"
    ).fetchall()

    conn.close()

    return {
        "summary": {
            "total_layoffs": total,
            "total_affected": total_affected,
            "avg_per_layoff": round(total_affected / total) if total else 0,
        },
        "by_industry": [{"industry": r[0], "count": r[1], "affected": r[2]} for r in by_industry],
        "by_month": [{"month": r[0], "count": r[1]} for r in by_month],
        "top_companies": [{"company": r[0], "total": r[1], "times": r[2]} for r in top_companies],
        "recent": [{"company": r[0], "affected": r[1], "date": r[2], "source": r[3]} for r in recent],
        "recurring": [{"company": r[0], "times": r[1], "total": r[2]} for r in recurring],
        "trends": [{"month": r[0], "count": r[1], "affected": r[2]} for r in trends],
    }


def update_company_history():
    """Update company layoff history and risk levels."""
    conn = get_db()

    companies = conn.execute(
        "SELECT company, COUNT(*) as times, SUM(employees_laid_off) as total, "
        "MIN(date) as first, MAX(date) as last "
        "FROM layoffs GROUP BY company"
    ).fetchall()

    for company, times, total, first, last in companies:
        avg = total / times if times else 0
        is_recurring = 1 if times > 1 else 0

        # Calculate risk level
        if times >= 3:
            risk = "critical"
        elif times >= 2:
            risk = "high"
        elif total and total > 1000:
            risk = "medium"
        else:
            risk = "low"

        conn.execute(
            """INSERT OR REPLACE INTO company_layoff_history
               (company, total_layoffs, total_employees_affected,
                first_layoff_date, last_layoff_date, layoff_count,
                avg_percentage, is_recurring, risk_level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company, times, total, first, last, times, avg, is_recurring, risk)
        )

    conn.commit()
    conn.close()
    log("Company history updated")


def get_company_risk(company_name):
    """Get layoff risk level for a company."""
    conn = get_db()
    result = conn.execute(
        "SELECT * FROM company_layoff_history WHERE LOWER(company) = LOWER(?)",
        (company_name,)
    ).fetchone()
    conn.close()

    if result:
        return {
            "company": result[0],
            "total_layoffs": result[1],
            "total_affected": result[2],
            "first_layoff": result[3],
            "last_layoff": result[4],
            "layoff_count": result[5],
            "is_recurring": bool(result[7]),
            "risk_level": result[8],
        }
    return None


# ── Dashboard HTML ───────────────────────────────────────────
def generate_dashboard_html():
    """Generate layoff tracker dashboard HTML."""
    analytics = get_layoff_analytics()

    # Build top companies table
    top_companies_html = ""
    for c in analytics["top_companies"][:15]:
        risk_class = "high" if c["times"] > 1 else "low"
        top_companies_html += f"""
        <tr>
            <td><strong>{c['company']}</strong></td>
            <td>{c['total']:,}</td>
            <td>{c['times']}</td>
            <td><span class="badge badge-{risk_class}">{'High Risk' if c['times'] > 1 else 'Low Risk'}</span></td>
        </tr>"""

    # Build trends
    trends_html = ""
    for t in analytics["trends"][:6]:
        bar_width = min(100, (t['count'] / max(1, analytics['summary']['total_layoffs'] / 6)) * 100)
        trends_html += f"""
        <div class="bar-row">
            <div class="bar-label">{t['month']}</div>
            <div class="bar"><div class="bar-fill" style="width:{bar_width}%"></div></div>
            <div class="bar-value">{t['count']}</div>
        </div>"""

    # Recent layoffs
    recent_html = ""
    for r in analytics["recent"][:10]:
        recent_html += f"""
        <tr>
            <td><strong>{r['company']}</strong></td>
            <td>{r['affected']:, if r['affected'] else 'N/A'}</td>
            <td>{r['date']}</td>
            <td><span class="badge">{r['source']}</span></td>
        </tr>"""

    # Risk companies
    risk_html = ""
    for r in analytics["recurring"][:10]:
        risk_class = "critical" if r['times'] >= 3 else "high"
        risk_html += f"""
        <div class="risk-card">
            <span class="risk-badge {risk_class}">{r['times']}x layoffs</span>
            <strong>{r['company']}</strong>
            <span>{r['total']:,} total affected</span>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title> Layoff Tracker — LeadFlow</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0e1a;color:#e2e8f0}}
.header{{background:linear-gradient(135deg,#dc2626,#ef4444);padding:24px 32px}}
.header h1{{font-size:24px;font-weight:700}}.header p{{color:#fecaca;margin-top:4px}}
.stats-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:20px 32px}}
.stat-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px}}
.stat-card .label{{color:#94a3b8;font-size:12px;text-transform:uppercase}}
.stat-card .value{{font-size:28px;font-weight:700;color:#ef4444;margin-top:4px}}
.stat-card.green .value{{color:#10b981}}
.section{{padding:0 32px 24px}}
.section h2{{font-size:18px;margin-bottom:12px;color:#f8fafc}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px;border-bottom:2px solid #334155;color:#94a3b8}}
td{{padding:8px;border-bottom:1px solid #1e293b}}
.badge{{padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}}
.badge-high{{background:#7f1d1d;color:#fca5a5}}
.badge-critical{{background:#991b1b;color:#fecaca}}
.badge-low{{background:#14532d;color:#86efac}}
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.bar-label{{width:80px;font-size:12px;color:#94a3b8}}
.bar{{flex:1;height:20px;background:#1e293b;border-radius:4px;overflow:hidden}}
.bar-fill{{height:100%;background:linear-gradient(90deg,#ef4444,#dc2626);border-radius:4px}}
.bar-value{{width:50px;font-size:12px;color:#94a3b8;text-align:right}}
.risk-card{{display:flex;align-items:center;gap:10px;padding:10px;background:#1e293b;border-radius:8px;margin-bottom:8px}}
.risk-badge{{padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
</style>
</head>
<body>
<div class="header">
<h1>🚨 Layoff Tracker</h1>
<p>Track layoffs, generate social media posts, drive organic traffic</p>
</div>

<div class="stats-row">
<div class="stat-card"><div class="label">Total Layoffs</div><div class="value">{analytics['summary']['total_layoffs']}</div></div>
<div class="stat-card"><div class="label">Total Affected</div><div class="value">{analytics['summary']['total_affected']:,}</div></div>
<div class="stat-card"><div class="label">Avg per Layoff</div><div class="value">{analytics['summary']['avg_per_layoff']:,}</div></div>
<div class="stat-card green"><div class="label">Risk Companies</div><div class="value">{len(analytics['recurring'])}</div></div>
</div>

<div class="section">
<h2>📊 Layoff Trends</h2>
{trends_html}
</div>

<div class="section grid-2">
<div>
<h2>🏢 Top Companies by Layoffs</h2>
<table>
<tr><th>Company</th><th>Total Affected</th><th>Times</th><th>Risk</th></tr>
{top_companies_html}
</table>
</div>
<div>
<h2>⚠️ High-Risk Companies (Multiple Layoffs)</h2>
{risk_html}
</div>
</div>

<div class="section">
<h2>📰 Recent Layoffs (Last 7 Days)</h2>
<table>
<tr><th>Company</th><th>Affected</th><th>Date</th><th>Source</th></tr>
{recent_html}
</table>
</div>

<p style="padding:20px 32px;color:#64748b;font-size:12px">Generated by LeadFlow Layoff Tracker | Data from layoffs.fyi, TechCrunch, Google News</p>
</body>
</html>"""
    return html


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrape", action="store_true", help="Scrape layoff data")
    parser.add_argument("--social", action="store_true", help="Generate social posts")
    parser.add_argument("--analytics", action="store_true", help="Show analytics")
    parser.add_argument("--dashboard", action="store_true", help="Generate dashboard")
    parser.add_argument("--platform", default="linkedin", choices=["linkedin", "twitter", "reddit"])
    parser.add_argument("--export", action="store_true", help="Export posts to files")
    parser.add_argument("--update-history", action="store_true", help="Update company history")
    parser.add_argument("--risk", help="Check risk for company")
    args = parser.parse_args()

    init_db()

    if args.scrape:
        scrape_all_layoffs()
    elif args.social:
        posts = generate_layoff_posts(args.platform, 10)
        for i, p in enumerate(posts, 1):
            print(f"\n--- Post {i} ---")
            print(p["text"])
    elif args.analytics:
        analytics = get_layoff_analytics()
        print(json.dumps(analytics, indent=2, default=str))
    elif args.dashboard:
        html = generate_dashboard_html()
        output = ROOT / "layoff_dashboard.html"
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Dashboard saved to {output}")
    elif args.export:
        filename = export_posts_to_files(args.platform)
        print(f"Posts exported to {filename}")
    elif args.update_history:
        update_company_history()
    elif args.risk:
        risk = get_company_risk(args.risk)
        if risk:
            print(json.dumps(risk, indent=2))
        else:
            print(f"No layoff history found for {args.risk}")
    else:
        print("Layoff Tracker")
        print("  --scrape           Scrape layoff data")
        print("  --social           Generate social posts")
        print("  --analytics        Show analytics")
        print("  --dashboard        Generate dashboard")
        print("  --export           Export posts to files")
        print("  --update-history   Update company history")
        print("  --risk <company>   Check company risk")


if __name__ == "__main__":
    main()
