#!/usr/bin/env bash
# FlowState one-command launcher (macOS / Linux)
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
PY=.venv/bin/python
$PY -m pip install -q -r backend/requirements.txt
[ -f data/networks/district.net.xml ] || (cd backend && ../$PY -m flowstate.cli build-network)
[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd backend && ../$PY -m flowstate.cli serve --port 8000) &
API=$!
trap 'kill $API 2>/dev/null' EXIT
echo "FlowState API -> http://127.0.0.1:8000   UI -> http://127.0.0.1:5173"
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173
