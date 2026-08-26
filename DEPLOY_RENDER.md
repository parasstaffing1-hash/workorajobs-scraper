# Deploy Workora Jobs on Render.com (FREE)

## Steps (10 minutes total)

### 1. Push to GitHub (2 min)
```bash
cd C:\Users\Administrator\Documents\ATS
git init
git add .
git commit -m "deploy to render"
git remote add origin https://github.com/YOUR/workorajobs.git
git push -u origin main
```

### 2. Deploy to Render (3 min)
1. Go to https://render.com
2. Sign up with GitHub (no credit card needed)
3. Click **New +** → **Web Service**
4. Connect your GitHub repo
5. Settings:
   - **Name**: workorajobs
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn scripts.workora_app:app --host 0.0.0.0 --port $PORT`
6. Click **Create Web Service**
7. Wait 5 minutes for build

### 3. Set Environment Variables (1 min)
In Render dashboard → Environment tab, add:

| Key | Value |
|-----|-------|
| R2_ACCOUNT_ID | (from Cloudflare) |
| R2_ACCESS_KEY_ID | (from Cloudflare) |
| R2_SECRET_ACCESS_KEY | (from Cloudflare) |
| R2_BUCKET_NAME | workorajobs |
| SMTP_HOST | smtp.gmail.com |
| SMTP_PORT | 587 |
| SMTP_USER | your@gmail.com |
| SMTP_PASS | your-app-password |
| EMAIL_FROM | alerts@workorajobs.com |

Click **Save** → **Manual Deploy** → **Deploy latest commit**

### 4. Set Up Keep-Alive (2 min)
Render sleeps after 15 min idle. Use free cron to keep it alive:

1. Go to https://cron-job.org (no credit card needed)
2. Sign up
3. Click **New Cron Job**
4. Settings:
   - **URL**: `https://workorajobs.onrender.com/api/health`
   - **Schedule**: Every 10 minutes (`*/10 * * * *`)
   - **Request Method**: GET
5. Save

### 5. Add Custom Domain (Optional - 2 min)
1. In Render dashboard → Settings → Custom Domains
2. Add `workorajobs.com`
3. Update DNS at your registrar:
   - CNAME: www → workorajobs.onrender.com
4. Render auto-provisions SSL

## Done!
Your site is live at: https://workorajobs.onrender.com

## Commands
```bash
# Check status
curl https://workorajobs.onrender.com/api/health

# Sync to R2 (run on your PC)
cd C:\Users\Administrator\Documents\ATS
.venv\Scripts\python.exe -m scripts.r2_sync sync
```

## Cost: $0/month
- Render: Free (750 hours/month)
- Cron-job.org: Free
- R2: Free (10GB)
- Gmail: Free
