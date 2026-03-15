#!/bin/bash
set -e

cd /home/matthewmurray/claude-memory

# Ensure venv exists
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Install in editable mode with all deps (idempotent)
.venv/bin/pip install -e ".[all,dev]" --quiet 2>/dev/null || true
