# 🚀 Workora Jobs - FREE $0/Month Deployment with R2

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              FREE $0/MONTH DEPLOYMENT                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Oracle Cloud (FREE Forever)     Cloudflare R2 (FREE)       │
│  ─────────────────────────      ────────────────────────    │
│  • 24GB RAM, 4 CPUs             • 10GB storage (FREE)       │
│  • 200GB disk                   • 10M requests/month        │
│  • Runs scrapers 24/7           • Never pauses (unlike       │
│  • Web app (FastAPI)              Supabase)                 │
│  • Email alerts                 • S3-compatible API          │
│  • SQLite database              • Global CDN                 │
│                                                              │
│  Gmail SMTP (FREE)              Vercel (FREE - Optional)    │
│  ──────────────────             ────────────────────────    │
│  • 500 emails/day               • Custom domain              │
│  • Job alerts                   • SSL certificate            │
│  • Welcome emails               • 100GB bandwidth            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Why R2 is Better Than Supabase

| Feature | R2 (Cloudflare) | Supabase |
|---------|-----------------|----------|
| **Storage** | 10GB free | 500MB free |
| **Egress** | **$0 always** | Charges after 2GB |
| **Requests** | 10M/month free | 50K/month |
| **Pause?** | **Never pauses** | Pauses after 7 days |
| **Credit card** | Not required | Not required |
| **Speed** | Global CDN | Single region |
| **S3 Compatible** | Yes (use boto3) | No |

---

## Step-by-Step Setup

### Step 1: Create Cloudflare R2 Bucket (5 minutes)

1. Go to **https://dash.cloudflare.com**
2. Sign up (free, no credit card needed)
3. Click **R2 Object Storage** in the sidebar
4. Click **Create bucket**
   - Name: `workora-jobs`
   - Location: Auto (closest to you)
   - Storage class: Standard
5. Click **Create bucket**

### Step 2: Create R2 API Token (2 minutes)

1. In R2 dashboard, click **Manage R2 API Tokens**
2. Click **Create API token**
   - Token name: `workora-jobs`
   - Permissions: **Object Read & Write**
   - Specify bucket: `workora-jobs`
3. Click **Create API Token**
4. **Copy immediately** (you won't see it again):
   - Access Key ID
   - Secret Access Key
   - Endpoint URL

### Step 3: Get Gmail App Password (2 minutes)

1. Go to **https://myaccount.google.com/apppasswords**
2. Select app: **Mail**
3. Select device: **Other (Custom name)** → Enter "Workora Jobs"
4. Click **Generate**
5. Copy the 16-character password

### Step 4: Connect Your Windows PC to R2 (5 minutes)

On your Windows PC:

```powershell
# Navigate to project
cd C:\Users\Administrator\Documents\ATS

# Create .env file
notepad .env
```

Add these lines to `.env`:

```bash
# R2 Storage (from Step 2)
R2_ACCOUNT_ID=your-cloudflare-account-id
R2_ACCESS_KEY_ID=your-access-key-id
R2_SECRET_ACCESS_KEY=your-secret-access-key
R2_BUCKET_NAME=workora-jobs

# Email Alerts (from Step 3)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-16-char-app-password
EMAIL_FROM=alerts@workorajobs.com
```

Save and close the file.

### Step 5: Sync Jobs to R2 (2 minutes)

```powershell
cd C:\Users\Administrator\Documents\ATS

# Install boto3 for R2 access
.venv\Scripts\pip.exe install boto3

# Sync all jobs to R2
.venv\Scripts\python.exe -m scripts.r2_sync sync

# Verify sync
.venv\Scripts\python.exe -m scripts.r2_sync status
```

### Step 6: Start 24/7 Scheduler (1 minute)

```powershell
cd C:\Users\Administrator\Documents\ATS

# Install scheduler
scripts\install_scheduler.bat
```

This starts:
- **Scraper**: Runs every 6 hours
- **R2 Sync**: Syncs new jobs every 2 hours
- **Database Backup**: Backs up daily to R2
- **Email Alerts**: Checks every hour
- **Health Monitor**: Checks every 5 minutes

### Step 7: Deploy Web Frontend to Vercel (3 minutes)

1. Push to GitHub:
```bash
cd C:\Users\Administrator\Documents\ATS
git add .
git commit -m "Deploy with R2"
git push
```

2. Go to **https://vercel.com**
3. Sign up with GitHub
4. Import your repository
5. Deploy
6. Add custom domain: `workorajobs.com`

---

## What Gets Stored in R2

| Path | Description | Size |
|------|-------------|------|
| `jobs/dedupe/*.json` | Individual job files (744K+ files) | ~2GB |
| `jobs/batches/*.json` | Batch exports | ~10MB each |
| `backups/latest.db.gz` | Compressed database backup | ~50MB |
| `backups/*.db.gz` | Daily backups (last 7 days) | ~350MB |
| `exports/*.csv` | CSV exports for download | ~100MB |
| `stats/latest.json` | Scraper statistics | 1KB |
| `reports/*.json` | Daily reports | 1KB each |

**Total: ~2.5GB** (well within 10GB free limit)

---

## R2 Sync Features

### Automatic Sync (Every 2 Hours)
- Syncs new jobs from SQLite to R2
- Only uploads new jobs (not duplicates)
- Updates stats in R2

### Daily Backup (Every 24 Hours)
- Compresses SQLite database
- Uploads to R2 as `.gz` file
- Keeps last 7 days of backups

### CSV Export (On Demand)
```powershell
.venv\Scripts\python.exe -m scripts.r2_sync export
```

### Manual Sync
```powershell
# Sync SQLite → R2
.venv\Scripts\python.exe -m scripts.r2_sync sync

# Sync R2 → SQLite (restore from backup)
.venv\Scripts\python.exe -m scripts.r2_sync download

# View status
.venv\Scripts\python.exe -m scripts.r2_sync status
```

---

## Monitoring

### Check R2 Status
```powershell
.venv\Scripts\python.exe -m scripts.r2_sync status
```

### Check Scheduler Logs
```powershell
type C:\Users\Administrator\Documents\ATS\scheduler.log
```

### Check Database Stats
```powershell
.venv\Scripts\python.exe -m scripts.scheduler_24_7 --stats
```

### View R2 Files
1. Go to **https://dash.cloudflare.com** → **R2**
2. Click your bucket
3. Browse files

---

## Troubleshooting

### "R2 not configured" Error
**Fix:** Make sure `.env` file has all R2 credentials.

### Sync Fails
**Fix:** Check R2 API token permissions. Make sure it has "Object Read & Write" access.

### Emails Not Sending
**Fix:** Check Gmail app password is correct. Try sending a test email.

### Scheduler Not Running
**Fix:** Run `scripts\install_scheduler.bat` as Administrator.

---

## Cost Summary

| Service | Plan | Monthly Cost |
|---------|------|--------------|
| Oracle Cloud | Always Free | $0 |
| Cloudflare R2 | Free (10GB) | $0 |
| Vercel | Free | $0 |
| Gmail SMTP | Free | $0 |
| **Total** | | **$0/month** |

---

## What You Get

- ✅ **744,372+ jobs** searchable
- ✅ **86,402+ company profiles**
- ✅ **150+ job sources** (LinkedIn, Indeed, Glassdoor, ATS)
- ✅ **User registration** and login
- ✅ **Saved jobs** and bookmarks
- ✅ **Job alerts** via email (daily at 9 AM)
- ✅ **Application tracking**
- ✅ **REST API** for mobile apps
- ✅ **SEO optimized** pages
- ✅ **24/7 auto-scraping** (every 6 hours)
- ✅ **R2 cloud backup** (every 2 hours)
- ✅ **Daily database backups** to R2
- ✅ **CSV exports** to R2
- ✅ **Health monitoring**
- ✅ **SSL certificate**
- ✅ **Custom domain**

**Total: $0/month forever! 🚀**

---

## Quick Reference

### Commands
```powershell
# Start 24/7 scheduler
scripts\install_scheduler.bat

# Sync to R2
.venv\Scripts\python.exe -m scripts.r2_sync sync

# Backup to R2
.venv\Scripts\python.exe -m scripts.r2_sync backup

# Export CSV
.venv\Scripts\python.exe -m scripts.r2_sync export

# Check status
.venv\Scripts\python.exe -m scripts.r2_sync status

# View logs
type scheduler.log
```

### URLs
- **App**: https://workorajobs.com
- **API**: https://workorajobs.com/api/health
- **R2**: https://dash.cloudflare.com → R2
- **Vercel**: https://vercel.com/dashboard

### Environment Variables
```bash
R2_ACCOUNT_ID=your-cloudflare-account-id
R2_ACCESS_KEY_ID=your-access-key-id
R2_SECRET_ACCESS_KEY=your-secret-access-key
R2_BUCKET_NAME=workora-jobs
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
EMAIL_FROM=alerts@workorajobs.com
```

---

**Ready to deploy?** Follow the steps above and your site will be live in 15 minutes! 🚀
