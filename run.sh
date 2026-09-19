#!/usr/bin/env bash
# Launch Tapo Web. Open http://localhost:8088 from your Windows browser
# (WSL2 forwards localhost to the host).
set -euo pipefail
cd "$(dirname "$0")"
HOST="${TAPO_WEB_HOST:-0.0.0.0}"
PORT="${TAPO_WEB_PORT:-8088}"
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
