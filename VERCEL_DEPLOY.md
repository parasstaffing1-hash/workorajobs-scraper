# 🚀 Deploy Workora Jobs to Vercel (Free)

## Quick Deploy (5 minutes)

### Step 1: Install Vercel CLI
```bash
npm install -g vercel
```

### Step 2: Login to Vercel
```bash
vercel login
```
Sign up with GitHub (free, no credit card needed)

### Step 3: Deploy
```bash
cd /c/Users/Administrator/Documents/ATS
vercel
```

Follow prompts:
- **Set up and deploy?** → Y
- **Which scope?** → Your account
- **Link to existing project?** → N
- **Project name:** workorajobs
- **Directory:** ./
- **Override settings?** → N

### Step 4: Deploy to Production
```bash
vercel --prod
```

### Step 5: Set Up Database (Required)

Vercel needs PostgreSQL. Use **Supabase** (free):

1. Go to https://supabase.com
2. Sign up free (no credit card)
3. Click "New Project"
4. **Name:** workora-jobs
5. **Password:** Choose a strong password
6. **Region:** Select closest to your users
7. Click "Create Project"
8. Go to Settings → Database
9. Copy the **Connection string** → **URI**

### Step 6: Set Environment Variables

```bash
vercel env add DATABASE_URL production
# Paste your Supabase connection string when prompted
```

### Step 7: Add Your Domain

1. Go to https://vercel.com/dashboard
2. Click your project → Settings → Domains
3. Add `workorajobs.com`
4. Add `www.workorajobs.com`
5. Update DNS:
   - Go to your domain registrar (Namecheap, GoDaddy, etc.)
   - Add CNAME record: `www` → `cname.vercel-dns.com`
   - Add A record: `@` → `76.76.21.21`

### Step 8: Migrate Data (Optional)

If you want to use your existing 734K jobs:

```bash
# Get Supabase connection string from Step 5
export DATABASE_URL="postgresql://postgres:password@db.xxx.supabase.co:5432/postgres"

# Run migration
python scripts/db_migrate.py
```

### Step 9: Set Up Scraper (Runs Separately)

Vercel serverless functions timeout after 30s, so run the scraper separately:

**Option A: Cron Job (Recommended)**
```bash
# Install cron on your computer or a cheap VPS
# Run scraper every hour
0 * * * * cd /path/to/workorajobs && python scripts/unified_lead_scraper --rounds 5
```

**Option B: Railway ($5 free)**
- Deploy scraper separately on Railway
- Connect to same Supabase database

**Option C: GitHub Actions (Free)**
```yaml
# .github/workflows/scrape.yml
name: Scrape Jobs
on:
  schedule:
    - cron: '0 * * * *'  # Every hour
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python scripts/unified_lead_scraper --rounds 5
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

---

## Complete Vercel Setup Commands

```bash
# 1. Install Vercel
npm install -g vercel

# 2. Login
vercel login

# 3. Deploy
cd /c/Users/Administrator/Documents/ATS
vercel

# 4. Production deploy
vercel --prod

# 5. Set database URL
vercel env add DATABASE_URL production

# 6. Redeploy with env
vercel --prod
```

---

## Environment Variables

| Variable | Value | Where to Get |
|----------|-------|--------------|
| `DATABASE_URL` | postgresql://... | Supabase Settings → Database |

---

## Free Tier Limits

| Feature | Limit |
|---------|-------|
| **Bandwidth** | 100 GB/month |
| **Build Time** | 6,000 min/month |
| **Serverless Exec** | 100 GB-hours |
| **Function Duration** | 10s (free) / 60s (pro) |
| **Domains** | 50 |

**For 734K jobs:** Free tier handles ~100K page views/month

---

## Troubleshooting

### App not loading?
```bash
vercel logs
```

### Database connection failed?
```bash
# Test connection
psql $DATABASE_URL
```

### CSS/JS not loading?
- Check `/static/css/main.css` exists
- Check `/static/js/main.js` exists

### Timeout errors?
- Upgrade to Pro ($20/month) for 60s timeout
- Or run scraper separately (see Step 9)

---

## Cost Breakdown

| Month | Cost |
|-------|------|
| Month 1-10 | $0 (free tier) |
| Month 11+ | $20/month (if needed) |
| Supabase | $0 (free 500MB) |
| **Total** | **$0 for 10 months** |

---

## Quick Deploy Button

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/yourname/workorajobs)

---

## Need Help?

- Vercel Docs: https://vercel.com/docs
- Supabase Docs: https://supabase.com/docs
- Deploy Guide: See DEPLOY_FREE.md in repo

**Your app will be live at: https://workorajobs.com** 🎉
