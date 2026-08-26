# 🚀 Workora Jobs - Production Deployment Guide

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WORKORA JOBS PLATFORM                     │
├─────────────────────────────────────────────────────────────┤
│  Vercel (Free)           │  Your Server (Windows)           │
│  ────────────────        │  ───────────────────────         │
│  • Web App (FastAPI)     │  • 24/7 Scraper Scheduler       │
│  • REST API              │  • SQLite Database (742K jobs)   │
│  • SEO Pages             │  • Email Alerts Sender          │
│  • SSL Certificate       │  • Health Monitor               │
│  • Custom Domain         │  • Auto-Restart                 │
├─────────────────────────────────────────────────────────────┤
│  Supabase (Free)         │  Data Sources                    │
│  ──────────────          │  ──────────────                  │
│  • PostgreSQL (backup)   │  • LinkedIn (255K jobs)          │
│  • User Auth             │  • Indeed (184K jobs)            │
│  • Real-time API         │  • Glassdoor, Dice, SimplyHired  │
│  • File Storage          │  • 200+ ATS Companies            │
│                          │  • 100+ YC Startups              │
└─────────────────────────────────────────────────────────────┘
```

## What You Need to Add

### 1. ✅ Already Built (Ready to Deploy)

| Component | Status | Description |
|-----------|--------|-------------|
| Web App | ✅ Ready | FastAPI with 15+ pages, SEO, auth |
| REST API | ✅ Ready | `/api/jobs`, `/api/stats`, `/api/health` |
| Database | ✅ Ready | 742K+ jobs in SQLite |
| 24/7 Scheduler | ✅ Ready | Auto-scraping every 6 hours |
| Email System | ✅ Ready | SMTP-based job alerts |
| Health Monitor | ✅ Ready | System health checks |
| Vercel Config | ✅ Ready | vercel.json configured |

### 2. 🔑 You Need to Provide

| Item | Where to Get | Cost |
|------|--------------|------|
| **Vercel Account** | https://vercel.com | Free |
| **Supabase Database** | https://supabase.com | Free |
| **Gmail App Password** | https://myaccount.google.com/apppasswords | Free |
| **Domain (Optional)** | https://namecheap.com or https://cloudflare.com | $10/year |

### 3. ⚙️ Environment Variables to Set

```bash
# In Vercel Dashboard → Settings → Environment Variables:
DATABASE_URL=postgresql://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres

# On your Windows server (set in Windows Environment Variables):
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
EMAIL_FROM=alerts@workorajobs.com
```

---

## Step-by-Step Deployment

### Step 1: Push to GitHub (2 minutes)

```bash
cd C:\Users\Administrator\Documents\ATS

# Initialize git (if not already)
git init
git add .
git commit -m "Workora Jobs - Production Ready"

# Create GitHub repo and push
git remote add origin https://github.com/YOUR_USERNAME/workorajobs.git
git push -u origin main
```

### Step 2: Deploy to Vercel (3 minutes)

1. Go to **https://vercel.com**
2. Click **"Sign Up"** → Use GitHub account
3. Click **"Add New Project"**
4. Import `workorajobs` repository
5. Settings:
   - **Framework Preset**: Other
   - **Root Directory**: `./`
   - **Install Command**: `pip install -r requirements.txt`
6. Click **"Deploy"**
7. Wait 2-3 minutes for deployment
8. You'll get a URL like `workorajobs.vercel.app`

### Step 3: Set Up Database (3 minutes)

1. Go to **https://supabase.com**
2. Click **"Start your project"** (free, no credit card)
3. Create new project:
   - **Project name**: workorajobs
   - **Database Password**: (create a strong password)
   - **Region**: Choose closest to your users
4. Wait 2 minutes for database to be ready
5. Go to **Settings** → **Database** → **Connection string**
6. Copy the **URI** (it looks like `postgresql://postgres.xxx:password@host:5432/postgres`)

### Step 4: Connect Database to Vercel (1 minute)

1. Go to Vercel Dashboard → Your project → **Settings**
2. Click **Environment Variables**
3. Add new variable:
   - **Key**: `DATABASE_URL`
   - **Value**: (paste the connection string from Step 3)
4. Click **Save**
5. Go to **Deployments** → Click the **"..."** menu → **Redeploy**

### Step 5: Migrate 742K Jobs to Database (2 minutes)

```bash
# On your Windows server
cd C:\Users\Administrator\Documents\ATS

# Set the database connection string
set DATABASE_URL=postgresql://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres

# Run migration
.venv\Scripts\python.exe -m scripts.migrate_to_postgres
```

This copies all 742K+ jobs to PostgreSQL in about 2 minutes.

### Step 6: Set Up Email Alerts (2 minutes)

1. Go to **https://myaccount.google.com/apppasswords**
2. Generate an App Password for "Mail"
3. Copy the 16-character password
4. On your Windows server, set environment variables:

```powershell
# Open PowerShell as Admin and run:
[System.Environment]::SetEnvironmentVariable("SMTP_USER", "your-email@gmail.com", "Machine")
[System.Environment]::SetEnvironmentVariable("SMTP_PASS", "your-16-char-app-password", "Machine")
[System.Environment]::SetEnvironmentVariable("SMTP_HOST", "smtp.gmail.com", "Machine")
[System.Environment]::SetEnvironmentVariable("SMTP_PORT", "587", "Machine")
[System.Environment]::SetEnvironmentVariable("EMAIL_FROM", "alerts@workorajobs.com", "Machine")
```

5. Restart the scheduler:
```bash
cd C:\Users\Administrator\Documents\ATS
scripts\install_scheduler.bat
```

### Step 7: Start 24/7 Scraping (1 minute)

```bash
cd C:\Users\Administrator\Documents\ATS

# Run the scheduler installer
scripts\install_scheduler.bat
```

This creates Windows scheduled tasks that:
- **Every 6 hours**: Run scrapers to fetch new jobs
- **Every hour**: Check and send email alerts
- **Every 10 minutes**: Monitor system health
- **Auto-restart**: If the scheduler crashes, it restarts automatically

### Step 8: Add Your Domain (1 minute)

1. Go to Vercel Dashboard → Your project → **Settings** → **Domains**
2. Add `workorajobs.com`
3. Vercel will show you DNS records to add
4. Go to your domain registrar (Namecheap, Cloudflare, etc.)
5. Add these DNS records:
   - **Type**: A, **Name**: @, **Value**: 76.76.21.21
   - **Type**: CNAME, **Name**: www, **Value**: cname.vercel-dns.com
6. Wait 5-10 minutes for DNS propagation

---

## Verification Checklist

After deployment, verify everything works:

```bash
# 1. Check health endpoint
curl https://workorajobs.com/api/health
# Should show: {"status":"ok","total_jobs":742000+}

# 2. Check homepage
curl https://workorajobs.com
# Should show HTML with job stats

# 3. Check API
curl "https://workorajobs.com/api/jobs?q=python&limit=5"
# Should return 5 Python jobs

# 4. Check scheduler is running
schtasks /query /tn "Workora_Scheduler"
# Should show "Running" status

# 5. Check scheduler logs
type C:\Users\Administrator\Documents\ATS\scheduler.log
```

---

## Monitoring Dashboard

To monitor your system in real-time:

```bash
# Check current stats
cd C:\Users\Administrator\Documents\ATS
.venv\Scripts\python.exe -m scripts.scheduler_24_7 --stats

# Watch scheduler logs
Get-Content C:\Users\Administrator\Documents\ATS\scheduler.log -Wait

# Check database growth
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('jobs.db'); print(f'Total: {c.execute(\"SELECT COUNT(*) FROM jobs\").fetchone()[0]:,}')"
```

---

## Troubleshooting

### Problem: Vercel deployment fails
**Solution**: Check that `requirements.txt` exists and has correct dependencies.

### Problem: Database connection fails
**Solution**: Check `DATABASE_URL` environment variable in Vercel settings.

### Problem: Scheduler not running
**Solution**: Run `scripts\install_scheduler.bat` as Administrator.

### Problem: Emails not sending
**Solution**: Check SMTP credentials in Windows environment variables.

### Problem: Jobs not updating
**Solution**: Check scheduler logs: `type scheduler.log`

---

## Cost Summary

| Service | Plan | Cost |
|---------|------|------|
| Vercel | Free | $0/month |
| Supabase | Free | $0/month |
| Gmail SMTP | Free | $0/month |
| Domain | Optional | $10/year |
| **Total** | | **$0/month** |

---

## What You Get

- ✅ **742,783+ jobs** searchable by keyword, location, company
- ✅ **86,320+ company profiles** with SEO optimization
- ✅ **150+ job sources** (LinkedIn, Indeed, Glassdoor, ATS platforms)
- ✅ **User registration** and login
- ✅ **Saved jobs** and bookmarks
- ✅ **Job alerts** via email (daily at 9 AM)
- ✅ **Application tracking** (save, applied, interview, offer)
- ✅ **REST API** for mobile apps and integrations
- ✅ **SEO optimized** (55K+ indexed pages)
- ✅ **24/7 auto-scraping** (every 6 hours)
- ✅ **Health monitoring** (auto-restart on failure)
- ✅ **SSL certificate** (automatic)
- ✅ **Custom domain** (workorajobs.com)

---

## Next Steps After Deployment

1. **Submit to Google Search Console** for faster indexing
2. **Add Google Analytics** for traffic tracking
3. **Set up social media** accounts for organic traffic
4. **Monitor scraper performance** daily
5. **Add more job sources** to increase to 1M+ jobs

---

**Need help?** Run `scripts\install_scheduler.bat` to set up 24/7 scraping, or contact support.
