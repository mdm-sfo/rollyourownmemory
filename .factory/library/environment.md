# Environment

## Required

- Python 3.12 in `.venv/`
- FastAPI + uvicorn + starlette (installed in .venv)
- SQLite with FTS5 support (system default)
- memory.db in project root

## Optional (for full functionality)

- ollama at localhost:11434 with llama3.3:70b (for Ask mode LLM synthesis)
- sentence-transformers + numpy (for semantic search)
- FAISS index file: memory.faiss + memory_ids.json

## Ports

- 8585: Web app (FastAPI + uvicorn)
- 11434: ollama (pre-existing, not managed by this app)

## Files

- `~/.claude/CLAUDE.md` — User's global Claude Code instructions (read/write)
- `~/.claude/memory-context.md` — Auto-generated context (read-only, regenerated via inject.py)
- `memory.db` — SQLite database (read/write)
