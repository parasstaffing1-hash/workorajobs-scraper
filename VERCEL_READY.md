# 🚀 Workora Jobs - Vercel Deployment Guide

## What's Built

✅ **742,783 jobs** from 150+ sources (LinkedIn, Indeed, Glassdoor, ATS platforms, YC startups)
✅ **86,320 company profiles** with SEO-optimized pages
✅ **Job search** with filters (keyword, location, company, source, time)
✅ **User system** - Registration, login, saved jobs, job alerts, application tracking
✅ **REST API** - `/api/jobs`, `/api/stats`, `/api/health`
✅ **SEO** - 55K+ URLs in sitemap, JSON-LD structured data, Open Graph tags
✅ **GDPR** cookie consent
✅ **Email system** for job alerts

---

## 🚀 Quick Deploy (10 minutes)

### Step 1: Push to GitHub (2 min)

```bash
cd C:\Users\Administrator\Documents\ATS
git init
git add .
git commit -m "Workora Jobs - 742K+ job platform"
git remote add origin https://github.com/YOUR_USERNAME/workorajobs.git
git push -u origin main
```

### Step 2: Deploy to Vercel (2 min)

1. Go to **https://vercel.com** → Sign up with GitHub
2. Click **"Add New Project"** → Import `workorajobs`
3. Settings:
   - Framework Preset: **Other**
   - Root Directory: `./`
   - Install Command: `pip install -r requirements.txt`
4. Click **Deploy**

### Step 3: Add Database (3 min)

1. Go to **https://supabase.com** → Sign up free
2. Create new project:
   - Name: `workorajobs`
   - Region: (closest to you)
3. Copy **Connection URI** from Settings → Database

### Step 4: Connect to Vercel (1 min)

1. Vercel Dashboard → Your project → **Settings** → **Environment Variables**
2. Add: `DATABASE_URL` = (paste connection string)
3. Go to **Deployments** → Click `...` → **Redeploy**

### Step 5: Migrate Data (2 min)

```bash
set DATABASE_URL=postgresql://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres
python -m scripts.migrate_to_postgres
```

### Step 6: Add Domain (1 min)

1. Vercel → **Settings** → **Domains** → Add `workorajobs.com`
2. Update DNS at your registrar:
   - A Record: `@` → `76.76.21.21`
   - CNAME: `www` → `cname.vercel-dns.com`

---

## 📁 Project Structure

```
ATS/
├── api/
│   └── index.py          # Vercel entry point
├── scripts/
│   ├── workora_app.py    # Main FastAPI app (1700+ lines)
│   ├── models.py         # Database models (users, jobs, alerts)
│   ├── migrate_to_postgres.py  # SQLite → PostgreSQL migration
│   └── supabase_schema.sql     # SQL schema for manual setup
├── static/
│   ├── css/main.css      # CSS styles
│   ├── js/main.js        # JavaScript
│   └── favicon.svg       # Site icon
├── vercel.json           # Vercel configuration
├── requirements.txt      # Python dependencies
├── Dockerfile            # Docker deployment
├── docker-compose.yml    # Docker Compose
└── jobs.db               # SQLite database (742K jobs)
```

## 🔑 Environment Variables

| Variable | Value | Where |
|----------|-------|-------|
| `DATABASE_URL` | `postgresql://...` | Vercel Settings |

## 📊 Database Stats

| Metric | Value |
|--------|-------|
| Total Jobs | 742,783 |
| Companies | 86,320 |
| Jobs This Week | 679,607 |
| Jobs Today | 9,728 |
| Data Sources | 150+ |

### Top Sources
- LinkedIn: 255,412 jobs
- Indeed: 184,524 jobs
- SimplyHired: 45,207 jobs
- GitHub Jobs: 102 jobs
- Greenhouse (200+ companies): ~100K jobs
- Lever (50+ companies): ~20K jobs
- SmartRecruiters (50+ companies): ~15K jobs
- YC Startups (100+): ~5K jobs

### Top Locations
- Bengaluru: 9,570 jobs
- Remote: 9,366 jobs
- New York: 8,162 jobs
- San Francisco: 7,324 jobs
- Hyderabad: 5,551 jobs
- Chennai: 4,957 jobs
- Seattle: 4,744 jobs
- Atlanta: 4,672 jobs
- Dallas: 4,659 jobs
- Chicago: 4,578 jobs

---

## 💰 Cost

| Service | Cost |
|---------|------|
| Vercel | $0 (100GB bandwidth) |
| Supabase | $0 (500MB database) |
| **Total** | **$0/month** |

---

## 🔧 Local Development

```bash
cd C:\Users\Administrator\Documents\ATS
.venv\Scripts\activate
python -m scripts.workora_app
# Open http://localhost:8000
```

## 📝 API Usage

```bash
# Search jobs
curl "http://localhost:8000/api/jobs?q=python&location=Delhi"

# Get stats
curl "http://localhost:8000/api/stats"

# Health check
curl "http://localhost:8000/api/health"
```

## 🌐 Pages

| Page | URL | Description |
|------|-----|-------------|
| Homepage | `/` | Job search, stats, features |
| Job Search | `/jobs?q=python` | Search with filters |
| Companies | `/companies` | 86K+ company profiles |
| Salary | `/salary` | Salary by skill/location |
| Layoffs | `/layoffs` | Layoff tracker |
| Register | `/login` | User signup |
| Dashboard | `/dashboard` | User dashboard |
| Saved Jobs | `/saved` | Bookmarked jobs |
| Alerts | `/alerts` | Job alerts |
| Applications | `/applications` | Job application tracker |
| API Docs | `/docs` | FastAPI documentation |

---

## 🚀 Ready to Deploy?

```bash
# One command deployment
cd C:\Users\Administrator\Documents\ATS
deploy-vercel.bat
```

Or follow the steps above manually.

**Need help?** See DEPLOY_NOW.md for quick reference.
