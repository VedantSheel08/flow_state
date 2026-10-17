#!/usr/bin/env bash
# Update the production backend from a committed git ref, with automatic rollback.
#   usage: deployment/server/deploy.sh <git-ref> [ssh-key]     (run from a clean checkout)
#   env:   FLOWSTATE_HOST (default root@137.220.52.142)
# Never touches systemd units, Caddy, the firewall or the venv. If the new ref needs new
# Python dependencies, install them first (see docs/DEPLOYMENT.md) and re-run.
set -euo pipefail
REF=${1:?git ref required}; KEY=${2:-$HOME/.ssh/flowstate_vultr}; HOST=${FLOWSTATE_HOST:-root@137.220.52.142}
SHA=$(git rev-parse --short "$REF"); TGZ=$(mktemp --suffix=.tgz)
git archive --format=tar.gz -o "$TGZ" "$REF"
SSH="ssh -i $KEY $HOST"
scp -q -i "$KEY" "$TGZ" "$HOST:/tmp/flowstate-$SHA.tgz"
$SSH bash -s "$SHA" <<'REMOTE'
set -uo pipefail
SHA=$1; APP=/opt/flowstate/app; R=/opt/flowstate/releases; TS=$(date +%Y%m%d-%H%M%S)
tar czf "$R/pre-$SHA-$TS.tgz" -C /opt/flowstate app          # snapshot of what is live now
restore() { echo "FAILED -> restoring previous release"; rm -rf "$APP"; tar xzf "$R/pre-$SHA-$TS.tgz" -C /opt/flowstate; systemctl restart flowstate; exit 1; }
rm -rf "$APP.new" && mkdir "$APP.new" && tar xzf "/tmp/flowstate-$SHA.tgz" -C "$APP.new" || exit 1
echo "$SHA" > "$APP.new/VERSION"; chown -R flowstate: "$APP.new"
rm -rf "$APP.old"; mv "$APP" "$APP.old" && mv "$APP.new" "$APP"; systemctl restart flowstate
for i in $(seq 1 30); do curl -fs localhost:8000/api/health | grep -q '"ok":true' && break; sleep 2; done
curl -fs localhost:8000/api/health | grep -q '"sumo":"[0-9]' || restore
curl -fs localhost:8000/api/scenarios | grep -q rush_hour || restore
rm -rf "$APP.old"; echo "deployed $SHA (previous snapshot: $R/pre-$SHA-$TS.tgz)"
REMOTE
rm -f "$TGZ"
curl -fsS https://api.flowstate.nyc/api/health >/dev/null && echo "public health OK"
