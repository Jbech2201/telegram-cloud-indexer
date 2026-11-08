#!/usr/bin/env bash
cd "$(dirname "$0")"

# Aktifkan virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 indexer.py
