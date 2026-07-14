#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$ROOT_DIR/ollama-models}"
export OLLAMA_ORIGINS="${OLLAMA_ORIGINS:-*}"

exec "$ROOT_DIR/ollama-local/bin/ollama" serve
