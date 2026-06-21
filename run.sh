#!/usr/bin/env bash
# Search Typeahead — one-command run (macOS / Linux)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f data/queries.txt ]; then
  echo "[run] generating dataset (first run) ..."
  python3 scripts/generate_dataset.py
fi

echo "[run] starting server on http://127.0.0.1:8000"
python3 -m backend.server
