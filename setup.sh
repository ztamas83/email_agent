#!/usr/bin/env bash
set -e

# Setup script for Proton Mail AI Triage Daemon using uv

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Proton Mail AI Triage Daemon Setup ==="

# 1. Check for uv
if ! command -v uv &> /dev/null; then
    echo "[*] 'uv' not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    if ! command -v uv &> /dev/null; then
        echo "[!] Failed to find uv in PATH. Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
fi
echo "[+] uv is available: $(uv --version)"

# 2. Setup virtual environment and dependencies
echo "[*] Creating virtual environment (.venv)..."
uv venv

echo "[*] Installing dependencies with uv..."
uv pip install -r requirements.txt

# 3. Environment configuration check
if [ ! -f ".env" ]; then
    echo "[*] .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "[!] Please make sure to edit .env and provide your actual credentials and API keys."
else
    echo "[+] Found existing .env file."
fi

# 4. Generate systemd service file
SERVICE_FILE="$SCRIPT_DIR/mail-triage.service"
CURRENT_USER="$(id -un)"

echo "[*] Generating systemd service file: $SERVICE_FILE"
cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Proton Mail AI Push Triage Daemon
After=proton-bridge.service
Requires=proton-bridge.service

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/.venv/bin/python daemon.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "[+] Service file generated successfully."
echo ""
echo "=== Next Steps ==="
echo "1. Edit your .env file with appropriate credentials:"
echo "   nano $SCRIPT_DIR/.env"
echo ""
echo "2. Install and enable the systemd service (requires sudo):"
echo "   sudo cp $SERVICE_FILE /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now mail-triage.service"
echo ""
echo "3. Run the web interface to view and filter audit logs:"
echo "   $SCRIPT_DIR/.venv/bin/python web.py"
echo "   (Open http://localhost:8000 in your browser)"
echo ""
echo "4. Check service status:"
echo "   sudo systemctl status mail-triage.service"
echo "   journalctl -u mail-triage.service -f"
echo ""
echo "[+] Setup completed!"
