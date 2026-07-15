#!/usr/bin/env bash
set -eo pipefail

cd /home/AiPrj
exec /home/AiPrj/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
