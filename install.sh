#!/usr/bin/env bash
set -e

# ==============================================================================
# Proton Mail AI Push Triage Daemon - Universal Installer
#
# Can be run directly:
#   ./install.sh
# Or fetched and executed via curl without GitHub tokens:
#   curl -sSL https://raw.githubusercontent.com/ztamas83/email_agent/master/install.sh | bash
#
# Optional environment variables:
#   INSTALL_DIR  - Directory to install into (default: ~/email_agent)
#   BRANCH       - Git branch to clone (default: master)
#   SERVICE_MODE - 'user' for systemd user, 'system' for /etc/systemd, 'none' to skip (default: auto)
#   SKIP_SERVICE - Set to 1 to skip systemd service registration
# ==============================================================================

REPO_HTTPS="https://github.com/ztamas83/email_agent.git"
BRANCH="${BRANCH:-master}"
TARBALL_URL="https://github.com/ztamas83/email_agent/archive/refs/heads/${BRANCH}.tar.gz"

# Text formatting
BOLD="\033[1m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

echo -e "${BOLD}${BLUE}=== Proton Mail AI Triage Daemon Installer ===${RESET}"

# 1. Determine Installation Directory
if [ -f "./daemon.py" ] && [ -f "./requirements.txt" ] && [ -f "./schemas.py" ]; then
    INSTALL_DIR="$(pwd)"
    echo -e "${GREEN}[+]${RESET} Using existing email_agent directory: ${BOLD}$INSTALL_DIR${RESET}"
else
    INSTALL_DIR="${INSTALL_DIR:-$HOME/email_agent}"
    echo -e "${BLUE}[*]${RESET} Target installation directory: ${BOLD}$INSTALL_DIR${RESET}"
    mkdir -p "$INSTALL_DIR"

    # Download source if not already present
    if [ ! -f "$INSTALL_DIR/daemon.py" ]; then
        if command -v git &>/dev/null; then
            echo -e "${BLUE}[*]${RESET} Cloning repository via public HTTPS..."
            git clone --depth 1 -b "$BRANCH" "$REPO_HTTPS" "$INSTALL_DIR"
        elif command -v curl &>/dev/null && command -v tar &>/dev/null; then
            echo -e "${BLUE}[*]${RESET} 'git' not found. Fetching archive via curl..."
            curl -sSL "$TARBALL_URL" | tar -xz --strip-components=1 -C "$INSTALL_DIR"
        elif command -v wget &>/dev/null && command -v tar &>/dev/null; then
            echo -e "${BLUE}[*]${RESET} 'git' not found. Fetching archive via wget..."
            wget -qO- "$TARBALL_URL" | tar -xz --strip-components=1 -C "$INSTALL_DIR"
        else
            echo -e "${RED}[!] Error: None of 'git', 'curl + tar', or 'wget + tar' are available.${RESET}"
            echo "    Please install git or curl to download the suite."
            exit 1
        fi
        echo -e "${GREEN}[+]${RESET} Source files installed to $INSTALL_DIR"
    fi
fi

cd "$INSTALL_DIR"

# 2. Check Python Prerequisites
if command -v python3 &>/dev/null; then
    PY_BIN="$(command -v python3)"
elif command -v python &>/dev/null; then
    PY_BIN="$(command -v python)"
else
    echo -e "${RED}[!] Error: Python 3 was not found.${RESET}"
    echo "    Please install Python 3.10 or higher:"
    echo "    - Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y python3 python3-venv"
    echo "    - Fedora/RHEL:   sudo dnf install -y python3"
    echo "    - Arch Linux:    sudo pacman -S python"
    exit 1
fi

PY_MAJOR="$($PY_BIN -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$($PY_BIN -c 'import sys; print(sys.version_info.minor)')"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "${RED}[!] Error: Python $($PY_BIN --version) detected. Python 3.10+ is required.${RESET}"
    exit 1
fi
echo -e "${GREEN}[+]${RESET} Python interpreter: $($PY_BIN --version) ($PY_BIN)"

# 3. Check / Install uv Package Manager
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv &>/dev/null; then
    echo -e "${BLUE}[*]${RESET} Installing 'uv' package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        if [ -x "$HOME/.local/bin/uv" ]; then
            UV_BIN="$HOME/.local/bin/uv"
        elif [ -x "$HOME/.cargo/bin/uv" ]; then
            UV_BIN="$HOME/.cargo/bin/uv"
        else
            echo -e "${RED}[!] Failed to locate 'uv' after install. Please check https://docs.astral.sh/uv/${RESET}"
            exit 1
        fi
    else
        UV_BIN="$(command -v uv)"
    fi
else
    UV_BIN="$(command -v uv)"
fi
echo -e "${GREEN}[+]${RESET} Package manager: $($UV_BIN --version)"

# 4. Create Virtual Environment & Install Dependencies
echo -e "${BLUE}[*]${RESET} Setting up Python virtual environment in $INSTALL_DIR/.venv..."
"$UV_BIN" venv "$INSTALL_DIR/.venv"

echo -e "${BLUE}[*]${RESET} Installing dependencies with uv..."
"$UV_BIN" pip install --python "$INSTALL_DIR/.venv/bin/python" -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}[+]${RESET} Dependencies successfully installed."

# 5. Initialize Configuration
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo -e "${BLUE}[*]${RESET} Generating .env configuration from .env.example..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo -e "${YELLOW}[!] Notice: Created $INSTALL_DIR/.env template.${RESET}"
else
    echo -e "${GREEN}[+]${RESET} Found existing .env file."
fi

# 6. Generate Systemd Service Files
CURRENT_USER="$(id -un)"
CURRENT_UID="$(id -u)"

# Daemon service file
DAEMON_SERVICE_FILE="$INSTALL_DIR/mail-triage.service"
cat <<EOF > "$DAEMON_SERVICE_FILE"
[Unit]
Description=Proton Mail AI Push Triage Daemon
After=network.target proton-bridge.service
Wants=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=-$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python daemon.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# Web UI service file
WEB_SERVICE_FILE="$INSTALL_DIR/mail-triage-web.service"
cat <<EOF > "$WEB_SERVICE_FILE"
[Unit]
Description=Proton Mail AI Triage Web Interface
After=network.target
Wants=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=-$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python web.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

echo -e "${GREEN}[+]${RESET} Generated systemd unit templates in $INSTALL_DIR"

# 7. Service Installation
SYSTEMD_AVAILABLE=false
if command -v systemctl &>/dev/null && [ -d /run/systemd/system ]; then
    SYSTEMD_AVAILABLE=true
fi

INSTALLED_SERVICE=false

if [ "$SYSTEMD_AVAILABLE" = true ] && [ "${SKIP_SERVICE:-0}" != "1" ] && [ "${SERVICE_MODE:-auto}" != "none" ]; then
    SERVICE_MODE="${SERVICE_MODE:-auto}"

    # Auto-detect mode
    if [ "$SERVICE_MODE" = "auto" ]; then
        if [ "$CURRENT_UID" -eq 0 ]; then
            SERVICE_MODE="system"
        elif [ -n "$XDG_RUNTIME_DIR" ] && [ -d "$XDG_RUNTIME_DIR/systemd" ]; then
            SERVICE_MODE="user"
        else
            SERVICE_MODE="user"
        fi
    fi

    if [ "$SERVICE_MODE" = "system" ]; then
        echo -e "${BLUE}[*]${RESET} Installing system-wide systemd service..."
        if [ "$CURRENT_UID" -eq 0 ]; then
            cp "$DAEMON_SERVICE_FILE" /etc/systemd/system/
            cp "$WEB_SERVICE_FILE" /etc/systemd/system/
            systemctl daemon-reload
            systemctl enable mail-triage.service
            systemctl enable mail-triage-web.service
            INSTALLED_SERVICE=true
            echo -e "${GREEN}[+]${RESET} System-wide service enabled."
        elif sudo -n true 2>/dev/null; then
            sudo cp "$DAEMON_SERVICE_FILE" /etc/systemd/system/
            sudo cp "$WEB_SERVICE_FILE" /etc/systemd/system/
            sudo systemctl daemon-reload
            sudo systemctl enable mail-triage.service
            sudo systemctl enable mail-triage-web.service
            INSTALLED_SERVICE=true
            echo -e "${GREEN}[+]${RESET} System-wide service enabled via sudo."
        else
            echo -e "${YELLOW}[!] Sudo password required for system-wide service install. Unit file saved at $DAEMON_SERVICE_FILE${RESET}"
        fi
    elif [ "$SERVICE_MODE" = "user" ]; then
        USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
        mkdir -p "$USER_SYSTEMD_DIR"
        
        # User service needs slightly modified WantedBy and no 'User=' field
        grep -v "^User=" "$DAEMON_SERVICE_FILE" > "$USER_SYSTEMD_DIR/mail-triage.service"
        grep -v "^User=" "$WEB_SERVICE_FILE" > "$USER_SYSTEMD_DIR/mail-triage-web.service"
        
        if systemctl --user daemon-reload 2>/dev/null; then
            systemctl --user enable mail-triage.service 2>/dev/null || true
            systemctl --user enable mail-triage-web.service 2>/dev/null || true
            INSTALLED_SERVICE=true
            echo -e "${GREEN}[+]${RESET} User service installed to $USER_SYSTEMD_DIR and enabled."
            
            # Offer loginctl linger reminder
            if command -v loginctl &>/dev/null; then
                loginctl enable-linger "$CURRENT_USER" 2>/dev/null || true
            fi
        else
            echo -e "${YELLOW}[!] Systemd user session not active. Unit files copied to $USER_SYSTEMD_DIR${RESET}"
        fi
    fi
fi

echo ""
echo -e "${BOLD}${GREEN}=== Installation Complete! ===${RESET}"
echo ""
echo -e "${BOLD}Next Steps:${RESET}"
echo -e "1. ${BOLD}Configure your credentials & API keys:${RESET}"
echo -e "   nano $INSTALL_DIR/.env"
echo -e "   (Set PROTON_USER, PROTON_PASS, and GEMINI_API_KEY)"
echo ""
if [ "$INSTALLED_SERVICE" = true ]; then
    if [ "$SERVICE_MODE" = "system" ]; then
        echo -e "2. ${BOLD}Start the background service:${RESET}"
        echo -e "   sudo systemctl start mail-triage.service"
        echo -e "   sudo systemctl start mail-triage-web.service"
        echo ""
        echo -e "3. ${BOLD}Check logs & status:${RESET}"
        echo -e "   sudo systemctl status mail-triage.service"
        echo -e "   journalctl -u mail-triage.service -f"
    else
        echo -e "2. ${BOLD}Start the background service:${RESET}"
        echo -e "   systemctl --user start mail-triage.service"
        echo -e "   systemctl --user start mail-triage-web.service"
        echo ""
        echo -e "3. ${BOLD}Check logs & status:${RESET}"
        echo -e "   systemctl --user status mail-triage.service"
        echo -e "   journalctl --user -u mail-triage.service -f"
    fi
else
    echo -e "2. ${BOLD}Start the daemon manually (or install systemd service):${RESET}"
    echo -e "   $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/daemon.py"
    echo -e "   $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/web.py"
fi
echo ""
echo -e "4. ${BOLD}Access the Web UI:${RESET}"
echo -e "   Open ${BOLD}http://localhost:8000${RESET} in your browser"
echo ""
