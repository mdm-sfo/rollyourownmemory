#!/usr/bin/env bash
set -euo pipefail

cd /home/matthewmurray/claude-memory

# Ensure venv exists
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -e ".[all,dev]"
fi

# Install fastapi if not already installed
.venv/bin/python -c "import fastapi" 2>/dev/null || .venv/bin/python -m pip install fastapi

# Verify port 8585 is available
if lsof -ti :8585 >/dev/null 2>&1; then
    echo "WARNING: Port 8585 is already in use"
fi

echo "Init complete. FastAPI ready."
