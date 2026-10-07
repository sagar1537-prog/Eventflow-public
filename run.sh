#!/usr/bin/env bash
# macOS / Linux: ./run.sh   (first run creates the virtual environment)
set -e
cd "$(dirname "$0")"
if [ ! -x venv/bin/python ]; then
  python3 -m venv venv
  venv/bin/python -m pip install -q --upgrade pip
  venv/bin/python -m pip install -q -r requirements.txt
fi
export HOST="${HOST:-0.0.0.0}" PORT="${PORT:-5000}"
echo "EventFlow on http://localhost:$PORT"
exec venv/bin/python app.py
