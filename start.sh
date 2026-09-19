#!/usr/bin/env bash
# Start Tapo Web in the background on http://localhost:8088 (idempotent).
# WSL stops everything when Windows shuts the VM down: just run this again.
cd "$(dirname "$0")"
if curl -s -m 2 "http://127.0.0.1:${TAPO_WEB_PORT:-8088}/health" >/dev/null; then echo "déjà démarré"; exit 0; fi
mkdir -p logs
TAPO_WEB_HOST="${TAPO_WEB_HOST:-127.0.0.1}" setsid nohup ./run.sh > logs/server.log 2>&1 < /dev/null &
for i in $(seq 1 20); do sleep 0.5; curl -s -m 1 "http://127.0.0.1:${TAPO_WEB_PORT:-8088}/health" >/dev/null && { echo "OK -> http://localhost:${TAPO_WEB_PORT:-8088}/#sd"; exit 0; }; done
echo "échec, voir logs/server.log"; exit 1
