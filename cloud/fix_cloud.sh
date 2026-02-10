#!/usr/bin/env bash
set -euo pipefail
cd ~/cloud-proxy
export $(tr -d '\r' < secrets.local.txt | sed -nE 's/^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"\r]*)"?$/\1=\2/p' | xargs)
if [ -z "${ALPACA_MASTER_KEY:-}" ] && [ -n "${ALPACA_API_KEY:-}" ]; then export ALPACA_MASTER_KEY="$ALPACA_API_KEY"; fi
if [ -z "${ALPACA_MASTER_SECRET:-}" ] && [ -n "${ALPACA_API_SECRET:-}" ]; then export ALPACA_MASTER_SECRET="$ALPACA_API_SECRET"; fi
if [ -z "${ALPACA_PROXY_TOKEN:-}" ]; then export ALPACA_PROXY_TOKEN="test_proxy"; fi
docker compose -f docker-compose.cloud-proxy.yml down
docker compose -f docker-compose.cloud-proxy.yml up -d --build
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"