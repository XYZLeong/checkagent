#!/bin/bash
# ============================================================
# deploy.sh — Deploy Drawing Checker Agent to Hostinger VPS
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh <server_ip> <ssh_user>
#
# Example:
#   ./deploy.sh 123.456.789.0 root
# ============================================================

set -e

SERVER_IP="${1}"
SSH_USER="${2:-root}"
REMOTE_DIR="/opt/agenticdocument"
WATCH_DIR="/opt/drawings"
N8N_PORT="5678"

if [ -z "$SERVER_IP" ]; then
  echo "Usage: ./deploy.sh <server_ip> [ssh_user]"
  echo "Example: ./deploy.sh 123.456.789.0 root"
  exit 1
fi

SSH_TARGET="${SSH_USER}@${SERVER_IP}"

echo ""
echo "=== Drawing Checker — Deploying to ${SSH_TARGET} ==="
echo ""

# Step 1: Create remote directories
echo "[1/5] Creating directories on server..."
ssh "$SSH_TARGET" "mkdir -p ${REMOTE_DIR} ${WATCH_DIR}"

# Step 2: Copy agent files
echo "[2/5] Copying agent files..."
scp agent.py extractor.py analyzer.py notifier.py config.py requirements.txt \
    "$SSH_TARGET:${REMOTE_DIR}/"

# Step 3: Install Python dependencies
echo "[3/5] Installing Python dependencies..."
ssh "$SSH_TARGET" "pip3 install -r ${REMOTE_DIR}/requirements.txt --quiet"

# Step 4: Create .env on server
echo "[4/5] Writing .env on server..."
ssh "$SSH_TARGET" "cat > ${REMOTE_DIR}/.env << 'EOF'
WATCH_DIR=${WATCH_DIR}
N8N_WEBHOOK_URL=http://localhost:${N8N_PORT}/webhook/drawing-alert
SETTLE_SECONDS=5
EOF"

# Step 5: Create and enable systemd service
echo "[5/5] Setting up systemd service..."
PYTHON_PATH=$(ssh "$SSH_TARGET" "which python3")
ssh "$SSH_TARGET" "cat > /etc/systemd/system/drawing-checker.service << EOF
[Unit]
Description=Drawing Checker Agent
After=network.target

[Service]
Type=simple
User=${SSH_USER}
WorkingDirectory=${REMOTE_DIR}
ExecStart=${PYTHON_PATH} ${REMOTE_DIR}/agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=drawing-checker

[Install]
WantedBy=multi-user.target
EOF"

ssh "$SSH_TARGET" "systemctl daemon-reload && systemctl enable drawing-checker && systemctl restart drawing-checker"

# Verify
echo ""
echo "=== Deployment complete! Checking status... ==="
echo ""
ssh "$SSH_TARGET" "systemctl status drawing-checker --no-pager"

echo ""
echo "=== Done ==="
echo ""
echo "Useful commands (run via SSH):"
echo "  Check status : systemctl status drawing-checker"
echo "  Live logs    : journalctl -u drawing-checker -f"
echo "  Restart      : systemctl restart drawing-checker"
echo "  Stop         : systemctl stop drawing-checker"
echo ""
echo "Watch folder on server : ${WATCH_DIR}"
echo "Update n8n Workflow 1 WATCH_DIR to: ${WATCH_DIR}"
echo ""
