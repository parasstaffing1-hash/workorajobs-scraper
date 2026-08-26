@echo off
REM ═══════════════════════════════════════════════════════════════
REM Job Scraper - Windows Deploy
REM ═══════════════════════════════════════════════════════════════
echo Job Scraper Deployment
echo =====================

REM Check Docker
where docker >NUL 2>NUL
if %ERRORLEVEL% neq 0 (
    echo Docker not found! Install from: https://docs.docker.com/get-docker/
    pause
    exit /B 1
)

REM Create .env if needed
if not exist .env (
    echo Creating .env...
    copy .env.example .env >NUL 2>NUL
    echo .env created
)

REM Build and start
echo.
echo Building Docker images...
docker compose build --no-cache

echo.
echo Starting services...
docker compose up -d

REM Wait
echo.
echo Waiting for services...
timeout /t 10 /nobreak >NUL

REM Check health
curl -s http://localhost:8000/api/health >NUL 2>NUL
if %ERRORLEVEL% equ 0 (
    echo API Server is healthy!
) else (
    echo API server still starting... check: docker compose logs api
)

echo.
echo ============================================
echo Deployment Complete!
echo.
echo Dashboard:  http://localhost:8000
echo API Health: http://localhost:8000/api/health
echo API Docs:   http://localhost:8000/docs
echo.
echo Commands:
echo   docker compose logs -f         (watch logs)
echo   docker compose restart         (restart all)
echo   docker compose down            (stop all)
echo ============================================
pause
