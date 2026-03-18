#!/usr/bin/env bash
# =============================================================================
# Prophet Engine — VPS Deployment Script
# Target: Ubuntu 22.04 LTS (fresh install)
# Run as: sudo bash deploy.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colours and helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "This script must be run as root (use sudo)."
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_USER="prophet"
APP_HOME="/home/${APP_USER}"
APP_DIR="${APP_HOME}/engine"
VENV_DIR="${APP_HOME}/venv"
PYTHON="python3.11"
SERVICE_NAME="prophet"
NGINX_CONF="/etc/nginx/sites-available/prophet"

# ---------------------------------------------------------------------------
# Step 0: Pre-flight checks
# ---------------------------------------------------------------------------
require_root

info "==================================================================="
info "  Prophet Engine — VPS Deployment"
info "  $(date)"
info "==================================================================="
echo

# Determine the source directory (where this script lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(dirname "${SCRIPT_DIR}")"   # engine/ directory
info "Source root: ${SOURCE_ROOT}"

# ---------------------------------------------------------------------------
# Step 1: System packages
# ---------------------------------------------------------------------------
info "Installing system packages..."

apt-get update -qq

# Python 3.11
add-apt-repository -y ppa:deadsnakes/ppa &>/dev/null || true
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

# PostgreSQL 16
apt-get install -y curl ca-certificates
install -d /usr/share/postgresql-common/pgdg
curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc
sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
    https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list'
apt-get update -qq
apt-get install -y postgresql-16

# Redis 7
curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/redis.list
apt-get update -qq
apt-get install -y redis

# Nginx + Certbot
apt-get install -y nginx certbot python3-certbot-nginx

# Utilities
apt-get install -y git build-essential libssl-dev libffi-dev

success "System packages installed."

# ---------------------------------------------------------------------------
# Step 2: Create system user
# ---------------------------------------------------------------------------
info "Creating system user '${APP_USER}'..."

if id "${APP_USER}" &>/dev/null; then
    warn "User '${APP_USER}' already exists — skipping creation."
else
    useradd --system --create-home --shell /bin/bash "${APP_USER}"
    success "User '${APP_USER}' created."
fi

# ---------------------------------------------------------------------------
# Step 3: PostgreSQL setup
# ---------------------------------------------------------------------------
info "Configuring PostgreSQL..."

systemctl enable postgresql
systemctl start postgresql

# Wait for PostgreSQL to be ready
for i in {1..10}; do
    pg_isready -U postgres && break
    sleep 2
done

# Create role and database (idempotent)
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${APP_USER}'" | grep -q 1 || \
    sudo -u postgres createuser --no-superuser --createdb "${APP_USER}"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${APP_USER}'" | grep -q 1 || \
    sudo -u postgres createdb "${APP_USER}" --owner="${APP_USER}"

success "PostgreSQL configured: user='${APP_USER}', database='${APP_USER}'."

# ---------------------------------------------------------------------------
# Step 4: Redis setup
# ---------------------------------------------------------------------------
info "Configuring Redis..."
systemctl enable redis-server
systemctl start redis-server
success "Redis started."

# ---------------------------------------------------------------------------
# Step 5: Copy application files
# ---------------------------------------------------------------------------
info "Copying application to ${APP_DIR}..."

if [[ -d "${APP_DIR}" ]]; then
    warn "${APP_DIR} already exists — backing up to ${APP_DIR}.bak.$(date +%s)"
    mv "${APP_DIR}" "${APP_DIR}.bak.$(date +%s)"
fi

cp -r "${SOURCE_ROOT}" "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
success "Application files copied."

# ---------------------------------------------------------------------------
# Step 6: Python virtual environment
# ---------------------------------------------------------------------------
info "Creating Python virtual environment at ${VENV_DIR}..."

sudo -u "${APP_USER}" "${PYTHON}" -m venv "${VENV_DIR}"
sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip wheel setuptools -q
sudo -u "${APP_USER}" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

success "Python environment ready."

# ---------------------------------------------------------------------------
# Step 7: Interactive .env configuration
# ---------------------------------------------------------------------------
ENV_FILE="${APP_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
    warn ".env already exists at ${ENV_FILE} — skipping interactive setup."
    warn "Edit it manually if you need to change any values."
else
    info "==================================================================="
    info "  Configuring environment (.env)"
    info "  Press ENTER to accept the default shown in [brackets]."
    info "==================================================================="
    echo

    # Helper: prompt with default
    prompt() {
        local var="$1" prompt_text="$2" default="$3"
        local value
        read -r -p "  ${prompt_text} [${default}]: " value
        printf '%s' "${value:-${default}}"
    }

    # Polymarket API credentials
    echo -e "\n${YELLOW}--- Polymarket API Credentials ---${NC}"
    echo "  Get these from: https://polymarket.com/profile > API Keys"
    PM_KEY=$(prompt "PM_KEY" "Polymarket API Key" "")
    PM_SECRET=$(prompt "PM_SECRET" "Polymarket API Secret" "")
    PM_PASS=$(prompt "PM_PASS" "Polymarket API Passphrase" "")
    PRIVATE_KEY=$(prompt "PRIVATE_KEY" "Polygon Wallet Private Key (hex, no 0x)" "")

    # Database
    echo -e "\n${YELLOW}--- Database ---${NC}"
    DB_URL=$(prompt "DB_URL" "PostgreSQL DSN" "postgresql+asyncpg://prophet:prophet@localhost/prophet")

    # Redis
    echo -e "\n${YELLOW}--- Redis ---${NC}"
    REDIS_URL=$(prompt "REDIS_URL" "Redis URL" "redis://localhost:6379/0")

    # API
    echo -e "\n${YELLOW}--- API Server ---${NC}"
    API_SECRET=$(prompt "API_SECRET" "Dashboard API Bearer Token (leave blank to auto-generate)" "")
    if [[ -z "${API_SECRET}" ]]; then
        API_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        info "Auto-generated API secret: ${API_SECRET}"
        info "SAVE THIS — you will need it in Vercel env vars."
    fi
    CORS_ORIGINS=$(prompt "CORS_ORIGINS" "Dashboard origin URL (Vercel URL)" "https://prophet-dashboard.vercel.app")

    # Trading mode (always start as paper)
    echo -e "\n${YELLOW}--- Trading Mode ---${NC}"
    warn "Paper trading is ALWAYS enabled at first. Change to false ONLY after 8+ weeks of validation."

    cat > "${ENV_FILE}" <<EOF
# Prophet Engine — Environment Configuration
# Generated by deploy.sh on $(date)
# =============================================================================

# Polymarket API (from https://polymarket.com/profile > API Keys)
POLYMARKET_API_KEY=${PM_KEY}
POLYMARKET_SECRET=${PM_SECRET}
POLYMARKET_PASSPHRASE=${PM_PASS}

# Polygon wallet private key (hex, no 0x prefix)
# Required for live trading. Keep this safe — never commit to git.
PRIVATE_KEY=${PRIVATE_KEY}
CHAIN_ID=137

# Database
DATABASE_URL=${DB_URL}

# Redis
REDIS_URL=${REDIS_URL}

# API Server
API_HOST=0.0.0.0
API_PORT=8000
API_SECRET=${API_SECRET}
CORS_ORIGINS=["${CORS_ORIGINS}"]

# Risk limits (USD)
MAX_POSITION_PER_MARKET=100.0
MAX_DAILY_LOSS=200.0
MAX_OPEN_POSITIONS=20
MAX_CONCENTRATION=0.25
MAX_DRAWDOWN_TOTAL=0.30
KILL_SWITCH=false

# Trading mode — DO NOT set to false until 8+ weeks of paper validation
PAPER_TRADING=true

# Scanner
SCAN_INTERVAL_MINUTES=15
TARGET_CRYPTOS=["BTC","ETH","SOL"]
EOF

    chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    success ".env created at ${ENV_FILE}"
fi

# ---------------------------------------------------------------------------
# Step 8: Run database migrations / setup
# ---------------------------------------------------------------------------
info "Running database setup..."

# Run alembic migrations if alembic is configured
if [[ -f "${APP_DIR}/alembic.ini" ]]; then
    sudo -u "${APP_USER}" bash -c "
        cd '${APP_DIR}' && \
        '${VENV_DIR}/bin/alembic' upgrade head
    "
    success "Alembic migrations applied."
else
    warn "alembic.ini not found — run 'alembic upgrade head' manually."
fi

# Run initial data seeding
if [[ -f "${APP_DIR}/scripts/setup_db.py" ]]; then
    sudo -u "${APP_USER}" bash -c "
        cd '${APP_DIR}' && \
        '${VENV_DIR}/bin/python' scripts/setup_db.py
    "
    success "Database seeded."
fi

# ---------------------------------------------------------------------------
# Step 9: systemd service
# ---------------------------------------------------------------------------
info "Creating systemd service file..."

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Prophet Trading Engine
Documentation=https://github.com/your-org/prophet
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m prophet.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=prophet
# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${APP_HOME}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl start "${SERVICE_NAME}"
success "systemd service '${SERVICE_NAME}' enabled and started."

# ---------------------------------------------------------------------------
# Step 10: Nginx reverse proxy
# ---------------------------------------------------------------------------
info "Configuring Nginx..."

# Read domain (optional)
read -r -p "  Enter your domain name (or press ENTER to use server IP only): " DOMAIN_NAME

cat > "${NGINX_CONF}" <<EOF
# Prophet Engine — Nginx reverse proxy
# Generated by deploy.sh on $(date)

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN_NAME:-_};

    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;

        # CORS is handled by FastAPI middleware; do not duplicate here
    }

    # Health check endpoint (no auth required from nginx side)
    location = /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_set_header Host \$host;
    }

    # Deny direct access to everything else
    location / {
        return 404;
    }
}
EOF

ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/prophet
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
success "Nginx configured."

# ---------------------------------------------------------------------------
# Step 11: HTTPS via Certbot (optional)
# ---------------------------------------------------------------------------
if [[ -n "${DOMAIN_NAME:-}" ]]; then
    echo
    info "To enable HTTPS, run:"
    echo "  sudo certbot --nginx -d ${DOMAIN_NAME}"
    echo "  sudo systemctl reload nginx"
    echo
    read -r -p "  Run certbot now? (y/N): " RUN_CERTBOT
    if [[ "${RUN_CERTBOT,,}" == "y" ]]; then
        certbot --nginx -d "${DOMAIN_NAME}" --non-interactive --agree-tos \
            --email "admin@${DOMAIN_NAME}" --redirect
        success "HTTPS enabled via Let's Encrypt."
    else
        warn "Skipping certbot. Run it manually when DNS is configured."
    fi
else
    warn "No domain specified — running HTTP only. Configure DNS first, then run:"
    warn "  sudo certbot --nginx -d your-domain.com"
fi

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------
echo
info "==================================================================="
success "  Prophet Engine deployment complete!"
info "==================================================================="
echo
echo "  Service status:    sudo systemctl status ${SERVICE_NAME}"
echo "  Live logs:         sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Configuration:     ${ENV_FILE}"
echo "  Application:       ${APP_DIR}"
echo

# Determine the API URL
SERVER_IP=$(hostname -I | awk '{print $1}')
if [[ -n "${DOMAIN_NAME:-}" ]]; then
    API_URL="http://${DOMAIN_NAME}/api"
else
    API_URL="http://${SERVER_IP}/api"
fi

echo "  API endpoint:      ${API_URL}"
echo "  Health check:      curl ${API_URL}/v1/health"
echo
echo "  Next steps:"
echo "  1. Verify the engine is healthy: curl ${API_URL}/v1/health"
echo "  2. Set NEXT_PUBLIC_API_URL=${API_URL} in Vercel"
echo "  3. Set NEXT_PUBLIC_API_TOKEN=<your API_SECRET> in Vercel"
echo "  4. Monitor logs: sudo journalctl -u ${SERVICE_NAME} -f"
echo

# Quick health check
sleep 3
if curl -sf "http://127.0.0.1:8000/health" &>/dev/null; then
    success "Health check passed — the engine is running!"
else
    warn "Health check failed. Check logs: journalctl -u ${SERVICE_NAME} -n 50"
fi
