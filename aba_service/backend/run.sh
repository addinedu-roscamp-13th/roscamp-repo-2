#!/usr/bin/env bash
# Start the RobotChatAI admin backend (FastAPI + uvicorn).
#
# Usage:
#   ./run.sh                # run on 0.0.0.0:8000 with --reload
#   HOST=127.0.0.1 PORT=9000 ./run.sh
#   RELOAD=0 ./run.sh       # disable auto-reload (production-ish)
set -euo pipefail

# Always operate from this script's directory (chatbot/backend).
cd "$(dirname "$(readlink -f "$0")")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-1}"

# Use the project virtualenv if present, otherwise fall back to PATH.
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  echo "warning: .venv not found, using system python" >&2
  PY="python3"
fi

# Warn if .env is missing — the app needs DATABASE_URL and JWT_SECRET.
if [[ ! -f ".env" ]]; then
  echo "warning: .env not found. Copy .env.example to .env and fill in values." >&2
fi

UVICORN_ARGS=(app.main:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" != "0" ]]; then
  UVICORN_ARGS+=(--reload)
fi

echo "Starting backend on http://$HOST:$PORT (docs at /docs)"
exec "$PY" -m uvicorn "${UVICORN_ARGS[@]}"
