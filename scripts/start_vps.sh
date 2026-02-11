#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

export TRADING_MODE="${TRADING_MODE:-OBSERVE}"
export RUNTIME_DB_PATH="${RUNTIME_DB_PATH:-$ROOT_DIR/runtime.db}"
export LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/live}"

mkdir -p "$LOG_DIR"

python3 -m scripts.run_system --mode "$TRADING_MODE" --db-path "$RUNTIME_DB_PATH" --log-dir "$LOG_DIR"
