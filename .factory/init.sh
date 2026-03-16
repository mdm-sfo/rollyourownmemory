#!/bin/bash
set -e

cd /home/matthewmurray/claude-memory

# Ensure venv exists and dependencies are installed
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -e ".[all,dev]" -q 2>/dev/null || true

echo "Environment ready."
