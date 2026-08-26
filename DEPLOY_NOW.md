# 🚀 Deploy Workora Jobs to Vercel (10 minutes)

## Step 1: Push to GitHub (2 min)

```bash
cd C:\Users\Administrator\Documents\ATS
git init
git add .
git commit -m "Workora Jobs - 742K+ jobs platform"
git remote add origin https://github.com/YOUR_USERNAME/workorajobs.git
git push -u origin main
```

## Step 2: Deploy to Vercel (3 min)

1. Go to https://vercel.com
2. Click "Sign Up" (use GitHub)
3. Click "New Project" → Import your GitHub repo
4. Settings:
   - Framework: **Other**
   - Root Directory: **.**
   - Build Command: **(leave empty)**
   - Output Directory: **(leave empty)**
5. Click "Deploy"
6. Wait for deployment (2 min)

## Step 3: Set up Database (3 min)

1. Go to https://supabase.com
2. Click "Start your project" (free, no credit card)
3. Create new project:
   - Name: workorajobs
   - Password: (any strong password)
   - Region: closest to you
4. Go to Settings → Database → Connection string → URI
5. Copy the connection string

## Step 4: Connect Database (1 min)

1. Go to Vercel Dashboard → your project → Settings → Environment Variables
2. Add new variable:
   - Name: `DATABASE_URL`
   - Value: (paste the Supabase connection string from Step 3)
3. Click "Save"
4. Go to Deployments → click "..." → Redeploy

## Step 5: Migrate Data (2 min)

```bash
cd C:\Users\Administrator\Documents\ATS
set DATABASE_URL=postgresql://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres
.venv\Scripts\python.exe -m scripts.migrate_to_postgres
```

## Step 6: Add Your Domain (1 min)

1. Go to Vercel Dashboard → your project → Settings → Domains
2. Add: `workorajobs.com`
3. Go to your domain registrar (Namecheap, Cloudflare, etc.)
4. Add DNS records:
   - Type: A, Name: @, Value: 76.76.21.21
   - Type: CNAME, Name: www, Value: cname.vercel-dns.com
5. Wait 5 minutes for DNS propagation

## ✅ Done!

Your site is now live at https://workorajobs.com

### What You Get (FREE):
- 742,000+ jobs searchable
- 88,000+ company profiles
- SEO-optimized pages (55K+ URLs)
- User registration & login
- Saved jobs & job alerts
- REST API
- SSL certificate
- Custom domain

### Cost: $0/month
- Vercel Free Tier: 100GB bandwidth
- Supabase Free Tier: 500MB database
