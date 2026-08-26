#!/bin/bash
# =============================================
#  Workora Jobs - Free Deployment Setup Script
# =============================================

set -e

echo "🚀 Workora Jobs Free Deployment Setup"
echo "======================================"
echo ""
echo "Choose your platform:"
echo "1) Oracle Cloud (Recommended - $0 forever)"
echo "2) Render.com (Free, needs ping every 10 min)"
echo "3) Local Windows PC"
echo ""
read -p "Enter choice (1-3): " choice

case $choice in
    1)
        echo ""
        echo "=== ORACLE CLOUD SETUP ==="
        echo ""
        echo "1. Go to https://cloud.oracle.com/"
        echo "2. Click 'Start for Free'"
        echo "3. Create VM instance:"
        echo "   - Name: workora-jobs"
        echo "   - Image: Ubuntu 22.04 (ARM64)"
        echo "   - Shape: VM.Standard.A1.Flex"
        echo "   - Resources: 4 OCPUs, 24 GB RAM"
        echo "   - Boot volume: 200 GB"
        echo "4. Download SSH key"
        echo "5. Wait for instance to be ready"
        echo ""
        read -p "Enter your VM public IP: " VM_IP
        read -p "Enter path to SSH key (e.g., ~/Downloads/key.pem): " SSH_KEY
        echo ""
        echo "Connecting to VM..."
        
        # Copy files to VM
        scp -i $SSH_KEY -r scripts/ requirements.txt vercel.json api/ static/ ubuntu@$VM_IP:~/workorajobs/
        scp -i $SSH_KEY jobs.db ubuntu@$VM_IP:~/workorajobs/
        
        # SSH and setup
        ssh -i $SSH_KEY ubuntu@$VM_IP << 'REMOTE'
            sudo apt update && sudo apt upgrade -y
            sudo apt install -y python3-pip python3-venv
            cd ~/workorajobs
            python3 -m venv venv
            source venv/bin/activate
            pip install -r requirements.txt
            
            # Create systemd service
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

[Install]
WantedBy=multi-user.target
EOF

            sudo systemctl daemon-reload
            sudo systemctl enable workora
            sudo systemctl start workora
            sudo ufw allow 80/tcp
            sudo ufw allow 443/tcp
            echo "✅ Server started on port 8000"
        REMOTE
        
        echo ""
        echo "✅ Setup complete!"
        echo "Visit: http://$VM_IP"
        ;;
        
    2)
        echo ""
        echo "=== RENDER.COM SETUP ==="
        echo ""
        echo "1. Go to https://render.com"
        echo "2. Sign up with GitHub"
        echo "3. Click 'New Web Service'"
        echo "4. Select your repository"
        echo "5. Settings:"
        echo "   - Name: workorajobs"
        echo "   - Runtime: Python"
        echo "   - Build Command: pip install -r requirements.txt"
        echo "   - Start Command: python -m uvicorn scripts.workora_app:app --host 0.0.0.0 --port \$PORT"
        echo "6. Click 'Create Web Service'"
        echo ""
        echo "7. To keep Render alive, set up cron at https://cron-job.org:"
        echo "   - URL: https://YOUR_APP.onrender.com/api/health"
        echo "   - Schedule: Every 10 minutes"
        ;;
        
    3)
        echo ""
        echo "=== WINDOWS PC SETUP ==="
        echo ""
        echo "1. Open Command Prompt as Administrator"
        echo "2. Run:"
        echo "   cd C:\\Users\\Administrator\\Documents\\ATS"
        echo "   scripts\\install_scheduler.bat"
        echo ""
        echo "3. Deploy to Vercel:"
        echo "   git add ."
        echo "   git commit -m 'deploy'"
        echo "   git push"
        echo "   Then go to vercel.com and deploy"
        ;;
        
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "======================================"
echo "✅ Setup Complete!"
echo "======================================"
