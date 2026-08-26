#!/bin/bash
# Workora Jobs - One-Click Deployment
# Usage: bash deploy.sh

set -e

echo "=== Workora Jobs Deployment ==="
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. Log out and back in, then re-run this script."
    exit 1
fi

# Check Docker Compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "Installing Docker Compose..."
    sudo apt-get install -y docker-compose-plugin
fi

# Create .env if missing
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    # Generate random secrets
    JWT_SECRET=$(openssl rand -hex 32)
    SESSION_SECRET=$(openssl rand -hex 32)
    sed -i "s/change-this-to-a-random-64-char-string-in-production/$JWT_SECRET/" .env
    sed -i "s/change-this-to-another-random-string/$SESSION_SECRET/" .env
    echo ".env created with random secrets. Edit it to add SMTP credentials."
fi

# Create logs directory
mkdir -p logs

# Start services
echo "Starting Workora Jobs..."
docker compose up -d --build

# Wait for health
echo "Waiting for API to start..."
sleep 10

# Check health
if curl -s http://localhost:8000/api/health | grep -q "ok"; then
    echo ""
    echo "✅ Workora Jobs is running!"
    echo ""
    echo "   URL:    http://localhost"
    echo "   API:    http://localhost/api/health"
    echo "   Admin:  http://localhost/api/docs"
    echo ""
    echo "To set up SSL (recommended for production):"
    echo "  sudo apt install certbot python3-certbot-nginx"
    echo "  sudo certbot --nginx -d workorajobs.com"
    echo ""
else
    echo "⚠️  API not responding yet. Check logs: docker compose logs api"
fi
