# Prophet Strategies — Deployment Guide

This guide covers deploying the Prophet engine to a VPS and the dashboard to Vercel.

---

## Section 1: Prerequisites

### VPS Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| OS       | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| RAM      | 2 GB    | 4 GB |
| CPU      | 1 vCPU  | 2 vCPU |
| Storage  | 40 GB SSD | 80 GB SSD |
| Network  | 100 Mbps | 1 Gbps |

Suitable providers: DigitalOcean, Hetzner, Vultr, Linode, AWS Lightsail.

**Recommended spec (~$10–20/month):** DigitalOcean Droplet — 2 GB RAM, 1 vCPU, 50 GB SSD.

### Domain Name (optional but recommended)

A domain name is required for HTTPS via Let's Encrypt. You can:
- Buy one from Namecheap, Cloudflare, or Google Domains (~$10–15/year).
- Point an A record to your VPS IP before running `certbot`.
- If you skip this, the API will run over HTTP on the server IP — acceptable for paper trading.

### Polymarket Account and API Keys

1. Create an account at [polymarket.com](https://polymarket.com).
2. Connect a Polygon wallet (MetaMask recommended).
3. Go to **Profile > API Keys > Create New Key**.
4. Note down three values:
   - **API Key** (`POLYMARKET_API_KEY`)
   - **Secret** (`POLYMARKET_SECRET`)
   - **Passphrase** (`POLYMARKET_PASSPHRASE`)
5. Export your wallet's private key (MetaMask: Account Details > Export Private Key).
   Store this securely — it is required for live trading only. Paper trading does not use it.

### Polygon Wallet with USDC (live trading only)

For live trading (future phase, after 8+ weeks paper validation):
- Fund your Polygon wallet with USDC via the Polymarket deposit flow.
- Start with a small amount ($200–500) while validating the live setup.
- The `PRIVATE_KEY` in `.env` must match the wallet you deposit from.

---

## Section 2: VPS Deployment

### Step 1: SSH into the VPS

```bash
ssh root@YOUR_VPS_IP
```

### Step 2: Upload the code

From your local machine, upload the `engine/` directory to the VPS:

```bash
# Option A: scp (simple)
scp -r engine/ root@YOUR_VPS_IP:/tmp/prophet-engine

# Option B: rsync (faster for subsequent uploads)
rsync -avz --exclude='.venv' --exclude='__pycache__' \
    engine/ root@YOUR_VPS_IP:/tmp/prophet-engine/
```

Alternatively, push to a private GitHub repository and clone it on the VPS:

```bash
git clone https://github.com/your-org/prophet.git /tmp/prophet-engine
```

### Step 3: Run the deployment script

```bash
cd /tmp/prophet-engine
sudo bash scripts/deploy.sh
```

The script will:
1. Install Python 3.11, PostgreSQL 16, Redis 7, Nginx, Certbot.
2. Create the `prophet` system user.
3. Set up the PostgreSQL database and user.
4. Copy the application to `/home/prophet/engine`.
5. Create a Python virtual environment at `/home/prophet/venv`.
6. Prompt you to enter each `.env` value interactively.
7. Run Alembic migrations and seed initial data.
8. Create and start the `prophet` systemd service.
9. Configure Nginx as a reverse proxy.
10. Optionally configure HTTPS via Certbot.

### Step 4: Configure the .env file

The deployment script creates `/home/prophet/engine/.env` interactively.
If you need to edit it after deployment:

```bash
sudo -u prophet nano /home/prophet/engine/.env
sudo systemctl restart prophet
```

Key values to verify:

```bash
# Required for Polymarket API access
POLYMARKET_API_KEY=your_api_key_here
POLYMARKET_SECRET=your_secret_here
POLYMARKET_PASSPHRASE=your_passphrase_here

# Must be true until 8+ weeks of paper trading validation
PAPER_TRADING=true

# Dashboard authentication token (share with Vercel as NEXT_PUBLIC_API_TOKEN)
API_SECRET=your_long_random_hex_string

# Allow your Vercel dashboard domain
CORS_ORIGINS=["https://your-dashboard.vercel.app"]
```

### Step 5: Verify the engine is running

```bash
# Check service status
sudo systemctl status prophet

# Health check via Nginx proxy
curl http://YOUR_VPS_IP/api/health

# Or directly to FastAPI (bypassing Nginx)
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "mode": "paper",
  "uptime_seconds": 42,
  "version": "1.0.0"
}
```

### Step 6: View logs

```bash
# Follow live logs
sudo journalctl -u prophet -f

# Last 100 lines
sudo journalctl -u prophet -n 100

# Logs since a specific time
sudo journalctl -u prophet --since "2026-01-01 00:00:00"
```

---

## Section 3: Dashboard Deployment (Vercel)

### Step 1: Push dashboard to GitHub

```bash
cd dashboard
git init
git add .
git commit -m "Initial dashboard commit"
git remote add origin https://github.com/your-org/prophet-dashboard.git
git push -u origin main
```

### Step 2: Connect to Vercel

1. Go to [vercel.com](https://vercel.com) and sign in.
2. Click **Add New Project**.
3. Import the `prophet-dashboard` GitHub repository.
4. Set the **Root Directory** to `dashboard` if your repo contains both `engine/` and `dashboard/`.
5. Leave Build Command as `npm run build` (auto-detected by Vercel).

### Step 3: Set environment variables in Vercel

In the Vercel project dashboard, go to **Settings > Environment Variables** and add:

| Variable | Value | Environment |
|----------|-------|-------------|
| `NEXT_PUBLIC_API_URL` | `https://your-domain.com/api` | Production, Preview |
| `NEXT_PUBLIC_API_TOKEN` | Your `API_SECRET` from `.env` | Production, Preview |

Note: `NEXT_PUBLIC_` prefix is required — Next.js only exposes variables with this prefix to the browser.

### Step 4: Deploy

Click **Deploy** in Vercel. The build should complete in ~2 minutes.

Subsequent deploys happen automatically on every `git push` to `main`.

To trigger a manual redeploy:
```bash
vercel --prod
```

### Step 5: Update CORS on the VPS

After you have your Vercel URL (e.g., `https://prophet-abc123.vercel.app`), update the VPS `.env`:

```bash
sudo -u prophet nano /home/prophet/engine/.env
# Set: CORS_ORIGINS=["https://prophet-abc123.vercel.app"]
sudo systemctl restart prophet
```

---

## Section 4: First Run Checklist

After deployment, verify each item:

- [ ] **Health endpoint responds**
  ```bash
  curl https://your-domain.com/api/health
  # Expected: {"status": "healthy", "mode": "paper"}
  ```

- [ ] **Markets are being scanned**
  ```bash
  curl -H "Authorization: Bearer YOUR_API_SECRET" \
       https://your-domain.com/api/markets
  # Expected: array of market objects (may be empty on first run, waits for scanner)
  ```

- [ ] **Order books collecting**
  ```bash
  # Check database for snapshots (within 5 minutes of start)
  sudo -u prophet psql prophet -c "SELECT count(*) FROM orderbook_snapshots;"
  ```

- [ ] **Dashboard loads correctly**
  - Open your Vercel URL in a browser.
  - Confirm the "PAPER TRADING" badge is visible in the header.
  - Confirm the system status shows "scanning" or "running".

- [ ] **Strategies are enabled**
  - Navigate to the Strategies page in the dashboard.
  - Confirm all 3 strategies show as enabled (green toggle).

- [ ] **Kill switch works**
  - Click the kill switch button on the dashboard.
  - Confirm the status changes to "killed" / "stopped".
  - Re-enable it by clicking again.

- [ ] **VPS survives a reboot**
  ```bash
  sudo reboot
  # Wait 60 seconds, then:
  curl https://your-domain.com/api/health
  ```

---

## Section 5: Monitoring and Maintenance

### Viewing logs

```bash
# Live log stream
sudo journalctl -u prophet -f

# Last 200 lines with timestamps
sudo journalctl -u prophet -n 200 --no-pager

# Filter by log level (errors only)
sudo journalctl -u prophet -p err -n 50
```

### Checking service health

```bash
sudo systemctl status prophet
```

### Restarting the service

```bash
sudo systemctl restart prophet
```

### Stopping / starting

```bash
sudo systemctl stop prophet
sudo systemctl start prophet
```

### Checking database

```bash
# Connect to PostgreSQL as the prophet user
sudo -u prophet psql prophet

# Useful queries:
\dt                                           -- list all tables
SELECT count(*) FROM markets;
SELECT count(*) FROM orderbook_snapshots;
SELECT count(*) FROM paper_orders WHERE status = 'open';
SELECT key, value FROM system_state;
```

### Updating the code

```bash
# 1. Upload new code (from local machine)
rsync -avz --exclude='.venv' --exclude='__pycache__' \
    engine/ root@YOUR_VPS_IP:/home/prophet/engine/

# 2. Install any new dependencies
sudo -u prophet /home/prophet/venv/bin/pip install -r /home/prophet/engine/requirements.txt

# 3. Apply database migrations
sudo -u prophet bash -c "
    cd /home/prophet/engine && \
    /home/prophet/venv/bin/alembic upgrade head
"

# 4. Restart the service
sudo systemctl restart prophet

# 5. Verify
sudo journalctl -u prophet -n 20
curl http://localhost:8000/health
```

### Disk space management

The database grows as order book snapshots accumulate. Check usage:

```bash
sudo -u prophet psql prophet -c "
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"

# Overall database size
sudo -u prophet psql prophet -c "SELECT pg_size_pretty(pg_database_size('prophet'));"
```

To clean up old snapshots (older than 90 days):
```bash
sudo -u prophet psql prophet -c "
DELETE FROM orderbook_snapshots
WHERE timestamp < NOW() - INTERVAL '90 days';
VACUUM ANALYZE orderbook_snapshots;
"
```

---

## Section 6: Going Live (Future Phase)

> **WARNING: Do NOT enable live trading until you have completed at least 8 weeks of paper trading with consistently positive results.**

### Prerequisites for live trading

Before switching to live mode, verify all of the following:

- [ ] 8+ weeks of paper trading completed.
- [ ] Paper trading shows net positive P&L across multiple strategy/market combinations.
- [ ] Win rate >= 40% with a profit factor >= 1.2.
- [ ] Maximum drawdown during paper period was within configured limits.
- [ ] You have reviewed every closed position and understand why it won or lost.
- [ ] Your Polygon wallet is funded with real USDC.
- [ ] You have reviewed Polymarket's terms of service.
- [ ] You understand the tax implications in your jurisdiction.

### Capital recommendations

Start conservatively:

| Week | Capital | Max position/market | Max daily loss |
|------|---------|---------------------|----------------|
| 1–2  | $200    | $20                 | $30            |
| 3–4  | $500    | $50                 | $75            |
| 5+   | Scale up based on Sharpe > 1.0 | | |

### Steps to enable live trading

1. Ensure your Polygon wallet private key is set in `.env`:
   ```bash
   sudo -u prophet nano /home/prophet/engine/.env
   # PRIVATE_KEY=your_hex_private_key_without_0x
   ```

2. Fund your Polymarket account with a small amount of USDC ($200–500).

3. Switch to live mode:
   ```bash
   sudo -u prophet nano /home/prophet/engine/.env
   # Change: PAPER_TRADING=true → PAPER_TRADING=false
   # Reduce risk limits for initial live period:
   # MAX_POSITION_PER_MARKET=20.0
   # MAX_DAILY_LOSS=30.0
   # MAX_OPEN_POSITIONS=5
   ```

4. Restart the engine:
   ```bash
   sudo systemctl restart prophet
   ```

5. Immediately verify on the dashboard:
   - The "PAPER TRADING" badge is gone (replaced by "LIVE").
   - No orders have been placed yet (first scan hasn't run).
   - Kill switch is accessible and functional.

6. Monitor closely for the first 48 hours:
   ```bash
   sudo journalctl -u prophet -f
   ```

7. If anything looks wrong: activate the kill switch immediately via the dashboard or:
   ```bash
   curl -X POST -H "Authorization: Bearer YOUR_API_SECRET" \
        https://your-domain.com/api/kill-switch
   ```

### Rolling back to paper trading

```bash
sudo -u prophet nano /home/prophet/engine/.env
# Set: PAPER_TRADING=true
sudo systemctl restart prophet
```

All open live positions remain open in the database. You will need to manually close them on Polymarket.
