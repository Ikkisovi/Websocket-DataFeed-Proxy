# Websocket-DataFeed-Proxy

A general WebSocket market-data proxy for local development, LAN, or cloud deployments.

**Goal:** connect once to your upstream data provider(s), then expose a single WebSocket endpoint that multiple downstream clients/strategies can share.

---

## Features

* Aggregates market data from upstream provider(s)
* Exposes one WebSocket endpoint for multiple downstream clients/strategies
* Supports Docker Compose (recommended) and a local/manual run mode (for debugging)

---

## Requirements

* Windows PowerShell (or bash/zsh equivalent)
* Docker + Docker Compose (recommended)
* (Optional) .NET SDK / Python runtime (depending on local mode)
* Network access to your upstream provider(s)

---

## Quick Start (Recommended: Docker Compose)

### 1) Clone

```bash
git clone https://github.com/Ikkisovi/Websocket-DataFeed-Proxy.git
cd Websocket-DataFeed-Proxy
```

### 2) Configure environment

If an example env file exists:

```powershell
copy .env.example .env
```

Then edit `.env` and fill in your provider credentials/endpoints and proxy ports.

### 3) Start services

```bash
docker compose up -d --build
```

### 4) Check status / logs

```bash
docker compose ps
docker compose logs -f
```

### 5) Stop

```bash
docker compose down
```

---

## Script Flow (run_strategy-style orchestration)

If you use an orchestration script (e.g., `run_strategy.ps1`), the expected flow is:

1. Prepare config / environment variables
2. Load API keys
3. Set proxy host/port
4. Choose runtime mode (local/direct/cloud)
5. Start proxy services (Compose or local process)
6. Wait until ready (health endpoint and/or WebSocket port)
7. Start strategy/client
8. Connect to the proxy WebSocket endpoint
9. Subscribe to target symbols/channels
10. Monitor runtime (logs/metrics)
11. On failure: restart proxy first, then restart client


---

## Manual Local Start (without Compose)

Use this when you need custom debugging or IDE-based runs.

1. Build/run the proxy service locally (project-specific command)
2. Confirm the configured port is listening
3. Start a downstream strategy/client
4. Verify the WebSocket handshake and incoming messages in logs

---

## Common Commands

```bash
# Start (compose)
docker compose up -d --build

# Rebuild + restart one service
docker compose build <service-name>
docker compose up -d <service-name>

# View logs
docker compose logs -f <service-name>

# Stop all
docker compose down
```

---

## Troubleshooting

### Git push rejected (fetch first)

If you see `push rejected (fetch first)`:

```bash
git pull origin main --rebase --allow-unrelated-histories
# then push again
```

### WebSocket connected but no data

* Verify provider credentials and endpoint configuration
* Confirm network access to the upstream provider
* Double-check subscription symbols/channels

### Port already in use

* Change the port in config/.env and restart services

### Connection drops

* Verify heartbeat/reconnect settings in both proxy and client
* Check firewall/security group/load balancer idle-timeout settings (cloud)
