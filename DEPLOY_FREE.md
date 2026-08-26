# 🚀 Workora Jobs - FREE $0/Month Deployment Plan

## 🏆 Plan A: Oracle Cloud Free Tier (BEST)

Oracle Cloud gives you **24GB RAM, 4 CPUs, 200GB storage FOREVER FREE**.

### Setup (15 minutes)

#### Step 1: Create Oracle Cloud Account
1. Go to https://cloud.oracle.com/
2. Click "Start for Free"
3. Fill in details (need a credit card - you will NOT be charged)
4. Select region closest to your users (e.g., "US East (Ashburn)" or "India West (Mumbai)")
5. Verify email and login

#### Step 2: Create Free VM Instance
1. Dashboard → "Create a VM Instance"
2. Name: `workora-jobs`
3. Image: **Canonical Ubuntu 22.04** (ARM64)
4. Shape: **VM.Standard.A1.Flex** (Always Free eligible)
5. Resources: **4 OCPUs, 24 GB RAM** (max free tier)
6. Add SSH keys: Generate new key pair → Download private key
7. Boot volume: **200 GB** (max free tier)
8. Click "Create"

Wait 3-5 minutes for instance to be ready.

#### Step 3: Connect to Your VM
```bash
# Open terminal and connect
ssh -i ~/Downloads/workora-jobs_key ubuntu@YOUR_PUBLIC_IP
```

#### Step 4: Install Everything on VM
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.10
sudo apt install -y python3.10 python3-pip python3-venv git

# Clone your project
git clone https://github.com/YOUR_USERNAME/workorajobs.git
cd workorajobs

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install scheduler dependencies
pip install psutil

# Copy your database from Windows
# On Windows, run:
scp -i ~/Downloads/workora-jobs_key your-username@YOUR_WINDOWS_IP:C:/Users/Administrator/Documents/ATS/jobs.db ~/

# Copy to VM
scp -i ~/Downloads/workora-jobs_key ubuntu@YOUR_PUBLIC_IP:~/jobs.db ~/workorajobs/
```

#### Step 5: Set Environment Variables
```bash
# Create .env file
cat > ~/.env << 'EOF'
DATABASE_URL=sqlite:///home/ubuntu/workorajobs/jobs.db
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-16-char-app-password
EMAIL_FROM=alerts@workorajobs.com
EOF
```

#### Step 6: Create Systemd Service (Auto-start on boot)
```bash
# Create service file
sudo tee /etc/systemd/system/workora.service << 'EOF'
[Unit]
Description=Workora Jobs Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/workorajobs
ExecStart=/home/ubuntu/workorajobs/venv/bin/python -m uvicorn scripts.workora_app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/.env

[Install]
WantedBy=multi-user.target
EOF

# Create scheduler service
sudo tee /etc/systemd/system/workora-scheduler.service << 'EOF'
[Unit]
Description=Workora Jobs Scheduler
After=network.target workora.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/workorajobs
ExecStart=/home/ubuntu/workorajobs/venv/bin/python scripts/scheduler_24_7.py
Restart=always
RestartSec=30
EnvironmentFile=/home/ubuntu/.env

[Install]
WantedBy=multi-user.target
EOF

# Start services
sudo systemctl daemon-reload
sudo systemctl enable workora
sudo systemctl enable workora-scheduler
sudo systemctl start workora
sudo systemctl start workora-scheduler
```

#### Step 7: Open Port 80
```bash
# Open port 80 for web access
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

#### Step 8: Test
```bash
# Check services
sudo systemctl status workora
sudo systemctl status workora-scheduler

# Check logs
sudo journalctl -u workora -f

# Test locally
curl http://localhost:8000/api/health
```

#### Step 9: Add Domain
1. Go to your domain registrar (Namecheap, Cloudflare)
2. Add DNS record:
   - Type: A
   - Name: @
   - Value: YOUR_VM_PUBLIC_IP
3. Wait 5 minutes
4. Visit: https://yourdomain.com

### ✅ Done! Your site is live at $0/month forever.

---

## 🥈 Plan B: Render + Vercel (No Oracle)

If Oracle Cloud is not available in your country, use Render + Vercel.

### Setup (10 minutes)

#### Step 1: Push to GitHub
```bash
cd C:\Users\Administrator\Documents\ATS
git add .
git commit -m "Free deployment"
git push
```

#### Step 2: Deploy to Render
1. Go to https://render.com
2. Sign up with GitHub
3. Click "New Web Service"
4. Select your repo
5. Settings:
   - Name: workorajobs
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python -m uvicorn scripts.workora_app:app --host 0.0.0.0 --port $PORT`
6. Click "Create Web Service"
7. Wait 5 minutes for deployment
8. Copy the URL (e.g., https://workorajobs.onrender.com)

#### Step 3: Set Up Cron Job (Keep Render Alive)
Render sleeps after 15 min inactivity. Use a free cron service to ping it.

1. Go to https://cron-job.org (free, no credit card)
2. Sign up
3. Create new cron job:
   - URL: `https://workorajobs.onrender.com/api/health`
   - Schedule: Every 10 minutes
   - Name: Keep Render Alive
4. Save

#### Step 4: Deploy to Vercel (Optional - Custom Domain)
1. Go to https://vercel.com
2. Import your GitHub repo
3. Deploy
4. Add custom domain

### ✅ Done! Your site is live at $0/month.

---

## 🥉 Plan C: Your Windows PC (Easiest)

If you have a Windows PC that can run 24/7.

### Setup (5 minutes)

#### Step 1: Run Scheduler
```bash
cd C:\Users\Administrator\Documents\ATS
scripts\install_scheduler.bat
```

#### Step 2: Deploy Web to Vercel
1. Push to GitHub
2. Deploy to Vercel
3. Set DATABASE_URL to point to your PC (or use SQLite on Vercel)

#### Step 3: Set Up Email Alerts
```powershell
[System.Environment]::SetEnvironmentVariable("SMTP_USER", "your@gmail.com", "Machine")
[System.Environment]::SetEnvironmentVariable("SMTP_PASS", "your-app-password", "Machine")
```

### ✅ Done! Cost: ~$5-10/month electricity.

---

## 📊 Cost Comparison

| Plan | Monthly Cost | 24/7 Scraping | Web Hosting | Email | Database |
|------|-------------|---------------|-------------|-------|----------|
| **Oracle Cloud** | **$0** | ✅ Always on | ✅ Always on | ✅ Gmail | ✅ 200GB |
| **Render + Vercel** | **$0** | ⚠️ Needs ping | ✅ Always on | ✅ Gmail | ⚠️ 30 days |
| **Windows PC** | **~$5-10** | ✅ Always on | ⚠️ Needs Vercel | ✅ Gmail | ✅ Unlimited |

---

## 🎯 My Recommendation

### If Oracle Cloud is available in your country:
**Use Oracle Cloud** - It's the BEST free option:
- 24GB RAM (more than enough)
- 4 CPUs (fast scraping)
- 200GB storage (plenty for 744K+ jobs)
- Always free (no time limit)
- No credit card charged

### If Oracle Cloud is NOT available:
**Use Render + Vercel**:
- Render for web + scrapers (free, sleeps after 15 min)
- Cron job every 10 minutes to keep it alive
- Vercel for custom domain (optional)

### If you want the EASIEST setup:
**Use your Windows PC**:
- Run scrapers on your PC
- Deploy web to Vercel
- Cost: ~$5-10/month electricity

---

## ⚡ Quick Start Commands

### Oracle Cloud:
```bash
# On Oracle VM
git clone https://github.com/YOUR_USERNAME/workorajobs.git
cd workorajobs
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
nohup python -m uvicorn scripts.workora_app:app --host 0.0.0.0 --port 8000 &
nohup python scripts/scheduler_24_7.py &
```

### Render:
```bash
# Push to GitHub
git push
# Then go to render.com and deploy
```

### Windows:
```bash
scripts\install_scheduler.bat
```

---

## 🔍 Monitoring

### Oracle Cloud:
```bash
ssh -i ~/Downloads/key ubuntu@YOUR_IP
sudo systemctl status workora
sudo journalctl -u workora -f
curl localhost:8000/api/health
```

### Render:
```bash
# Check logs on render.com dashboard
# Or use curl
curl https://workorajobs.onrender.com/api/health
```

### Windows:
```bash
type C:\Users\Administrator\Documents\ATS\scheduler.log
.venv\Scripts\python.exe -m scripts.scheduler_24_7 --stats
```

---

## 📝 What You Get for $0

- ✅ 744,372+ jobs searchable
- ✅ 86,402+ company profiles
- ✅ 150+ job sources (LinkedIn, Indeed, Glassdoor, ATS platforms)
- ✅ User registration and login
- ✅ Saved jobs and bookmarks
- ✅ Job alerts via email (daily at 9 AM)
- ✅ Application tracking
- ✅ REST API
- ✅ SEO optimized pages
- ✅ 24/7 auto-scraping
- ✅ Health monitoring
- ✅ SSL certificate
- ✅ Custom domain

**Total: $0/month forever!**

---

## 🆘 Need Help?

1. **Oracle Cloud issues**: Check https://docs.oracle.com/en-us/iaas/Content/home.htm
2. **Render issues**: Check https://render.com/docs
3. **Vercel issues**: Check https://vercel.com/docs
4. **Scheduler issues**: Check scheduler.log file

**Good luck! 🚀**
