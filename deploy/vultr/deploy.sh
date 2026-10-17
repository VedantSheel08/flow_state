#!/usr/bin/env bash
# Usage: deploy/vultr/deploy.sh user@server-ip   (run from the repository root; needs ssh access and a filled-in deploy/vultr/.env)
set -euo pipefail
host="${1:?usage: deploy.sh user@host}"
[ -f deploy/vultr/.env ] || { echo "create deploy/vultr/.env from .env.example first" >&2; exit 1; }
scp -q deploy/vultr/.env "$host:/opt/flowstate/deploy/vultr/.env"
ssh "$host" 'chmod 600 /opt/flowstate/deploy/vultr/.env && cd /opt/flowstate/deploy/vultr && docker compose --env-file .env up -d --build'
echo "deployed; check: ssh $host 'curl -s localhost:8010/api/experiments/status'"
