@echo off
echo ==========================================
echo   Deploy to Render.com (FREE)
echo ==========================================
echo.
echo Steps:
echo 1. Go to https://render.com and sign up
echo 2. Click "New +" then "Web Service"
echo 3. Connect your GitHub repo
echo 4. Use these settings:
echo    - Build Command: pip install -r requirements.txt
echo    - Start Command: uvicorn scripts.workora_app:app --host 0.0.0.0 --port %%PORT%%
echo.
echo 5. Add environment variables in Render dashboard
echo.
echo 6. Go to https://cron-job.org and ping your app every 10 minutes
echo.
echo ==========================================
echo.
echo Pushing to GitHub...
cd /d "%~dp0"
git init
git add .
git commit -m "deploy to render"
git remote add origin https://github.com/YOUR_USERNAME/workorajobs.git 2>nul
git push -u origin main
echo.
echo Done! Now go to render.com to finish setup.
pause
