@echo off
echo ========================================
echo   Workora Jobs - Free Deployment Setup
echo ========================================
echo.

echo Choosing the best free platform for you:
echo.
echo 1. RENDER (Recommended - Easiest)
echo    - Free 750 hrs/month
echo    - Auto SSL, auto deploy
echo    - https://render.com
echo.
echo 2. RAILWAY ($5 Free Credit)
echo    - One-click deploy
echo    - Lasts ~2 months free
echo    - https://railway.app
echo.
echo 3. KOYEB (No Credit Card)
echo    - Free tier available globally
echo    - Fast deployment
echo    - https://koyeb.com
echo.
echo ========================================
echo.
echo PUSHING TO GITHUB...
echo.

cd /d "%~dp0"
git init
git add .
git commit -m "Workora Jobs - Production ready"
git remote add origin https://github.com/YOUR_USERNAME/workorajobs.git
git push -u origin main --force

echo.
echo ========================================
echo GITHUB READY!
echo.
echo NOW GO TO YOUR CHOSEN PLATFORM:
echo.
echo OPTION A - RENDER (Recommended):
echo 1. Go to https://render.com
echo 2. Sign up (free, no credit card)
echo 3. Click "New +" > "Web Service"
echo 4. Connect your GitHub repo
echo 5. Settings:
echo    - Build: pip install -r requirements.txt
echo    - Start: python -m scripts.workora_app
echo 6. Add PostgreSQL database (free)
echo 7. Add env var: DATABASE_URL
echo.
echo OPTION B - RAILWAY:
echo 1. Go to https://railway.app
echo 2. Sign up with GitHub
echo 3. Click "Deploy from GitHub"
echo 4. Select workorajobs repo
echo 5. Add PostgreSQL (auto-linked)
echo.
echo OPTION C - KOYEB:
echo 1. Go to https://koyeb.com
echo 2. Sign up (no credit card needed)
echo 3. Create App > Import from GitHub
echo 4. Select workorajobs repo
echo 5. Build: pip install -r requirements.txt
echo 6. Run: python -m scripts.workora_app
echo.
echo After deploy, add your domain workorajobs.com
echo ========================================
pause
