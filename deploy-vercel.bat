@echo off
echo ========================================
echo   Workora Jobs - Vercel Deployment
echo ========================================
echo.

REM Step 1: Install Vercel CLI
echo [1/5] Installing Vercel CLI...
npm install -g vercel 2>nul
echo.

REM Step 2: Login to Vercel
echo [2/5] Login to Vercel (browser will open)...
vercel login
echo.

REM Step 3: Deploy to Vercel
echo [3/5] Deploying to Vercel...
cd /d "%~dp0"
vercel --yes
echo.

REM Step 4: Deploy to Production
echo [4/5] Deploying to production...
vercel --prod --yes
echo.

REM Step 5: Instructions
echo [5/5] Deployment complete!
echo.
echo NEXT STEPS:
echo 1. Go to https://vercel.com/dashboard
echo 2. Click on your project
echo 3. Go to Settings - Environment Variables
echo 4. Add: DATABASE_URL = your-supabase-connection-string
echo 5. Go to Settings - Domains
echo 6. Add: workorajobs.com
echo.
echo Get Supabase at: https://supabase.com (free)
echo After getting Supabase URL, run:
echo   python -m scripts.migrate_to_postgres
echo.
echo DONE! Your site is live at your Vercel URL.
pause
