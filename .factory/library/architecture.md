# Architecture

## Web App Architecture

- **Backend:** FastAPI in `src/web.py`, served by uvicorn on port 8585
- **Frontend:** Vanilla HTML/CSS/JS in `static/` directory, served as static files by FastAPI
- **Database:** SQLite `memory.db` accessed via `src/memory_db.py` (get_conn() for WAL + busy_timeout)
- **LLM:** ollama at localhost:11434 for Ask mode synthesis (llama3.3:70b)
- **Semantic search:** FAISS index via `src/embed.py` search_similar()

## Existing Pipeline (DO NOT MODIFY)

- `src/ingest.py` — JSONL -> SQLite ETL
- `src/embed.py` — Sentence-transformer embeddings + FAISS
- `src/distill.py` — LLM fact extraction
- `src/entities.py` — Entity extraction
- `src/inject.py` — Context generation (called via subprocess for preview)
- `src/curate.py` — CLI fact curation
- `src/mcp_server.py` — MCP tools server
- `src/memory_db.py` — Shared DB functions (used by web app)

## Key Patterns

- All DB connections use `memory_db.get_conn()` which sets WAL mode + busy_timeout=5000
- FTS5 queries can throw sqlite3.OperationalError on syntax errors — always wrap in try/except
- Semantic search may be unavailable if FAISS index or sentence-transformers missing — always fallback to FTS
- The web app is read-heavy with occasional writes (fact edits, CLAUDE.md saves)
