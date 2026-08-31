# 24/7 Worker Deployment

The Streamlit dashboard is the UI. The always-on Python worker must run on an always-on Linux server/VPS. Do not rely on a browser session or Streamlit Community Cloud to host the background worker.

## Recommended single-server layout

```text
/opt/stockmarket-bot
  ├── Streamlit dashboard (app.py)
  └── scripts/worker.py  <-- systemd, always running
```

The worker and dashboard share `/opt/stockmarket-bot/data`, including the SQLite journal and `worker_heartbeat.json`.

## Install on the VPS

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin stockbot || true
sudo mkdir -p /opt/stockmarket-bot
sudo chown -R stockbot:stockbot /opt/stockmarket-bot

sudo -u stockbot git clone https://github.com/kvrmsp18/stockmarket-bot.git /opt/stockmarket-bot
cd /opt/stockmarket-bot
sudo -u stockbot python3 -m venv .venv
sudo -u stockbot .venv/bin/pip install -r requirements.txt
```

Create `/etc/stockmarket-bot.env` with the broker/data/AI/Telegram environment variables required by the application. Never commit this file.

At minimum for real Dhan market data:

```text
DHAN_CLIENT_ID=...
DHAN_ACCESS_TOKEN=...
DHAN_SECURITY_IDS_JSON=...
BOT_MODE=PAPER
DHAN_LIVE_TRADING_ENABLED=false
```

Install and start the service:

```bash
sudo cp deploy/stockmarket-worker.service /etc/systemd/system/stockmarket-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now stockmarket-worker
sudo systemctl status stockmarket-worker --no-pager
```

## Verify the heartbeat

The worker writes `/opt/stockmarket-bot/data/worker_heartbeat.json` every 30 seconds, independently of the five-minute market cycle.

```bash
cat /opt/stockmarket-bot/data/worker_heartbeat.json
```

Expected healthy state:

```text
state: RUNNING
message: Heartbeat OK
market_open: true/false
cycle_interval_seconds: 300
heartbeat_interval_seconds: 30
```

Also verify the service itself:

```bash
systemctl is-enabled stockmarket-worker
systemctl is-active stockmarket-worker
journalctl -u stockmarket-worker -n 100 --no-pager
```

`is-enabled` must be `enabled` and `is-active` must be `active`.

## Why this is separate from Streamlit

A Streamlit app is an interactive UI process and is not the correct place to guarantee a 24/7 market worker. The worker must continue when no browser is open. Systemd restarts it automatically after a crash or server reboot.

The dashboard reads the same persisted data directory and heartbeat file when the dashboard and worker are deployed on the same server.

## Safety

This deployment keeps `BOT_MODE=PAPER` and `DHAN_LIVE_TRADING_ENABLED=false`. It is intended for end-to-end paper testing. The live broker order endpoint must not be enabled as part of heartbeat deployment.
