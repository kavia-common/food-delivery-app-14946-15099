#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
PORT="${PORT:-8102}"
HOST="${HOST:-0.0.0.0}"
exec uvicorn main:app --host "${HOST}" --port "${PORT}" --reload
