# Architecture

**What belongs here:** Architectural decisions, patterns, module responsibilities.

---

## Module Responsibilities

- `memory_db.py` — shared DB access layer: connections, migrations, FTS/semantic queries
- `embed.py` — embedding engine: model loading, message embedding, FAISS index, semantic search
- `distill.py` — fact extraction pipeline: heuristic + LLM extraction, dedup, backfill
- `mcp_server.py` — MCP tool surface for Claude Code integration
- `web.py` — FastAPI web UI: search, ask, facts CRUD, sessions, CLAUDE.md editor
- `ingest.py` — JSONL log ingestion into messages table
- `inject.py` — context injection into CLAUDE.md
- `entities.py` — entity extraction and relationship tracking

## Key Patterns

- **Import fallback**: All cross-module imports use `try: from src.X except ImportError: from X`
- **Graceful degradation**: Semantic features wrapped in try/except; fall back to FTS-only
- **In-memory test DB**: Tests use `conftest.py` `db` fixture with schema.sql loaded into `:memory:`
- **Migration system**: `migrate_schema()` in memory_db.py applies idempotent migrations (1-6 exist; 6 adds fact_embeddings)
- **FTS5 sync**: Triggers keep FTS tables in sync with source tables automatically
- **Partial model caching**: `get_model()` in embed.py creates a new SentenceTransformer instance on every call with no caching. However, `_get_dedup_model()` in distill.py uses a global `_embedding_model` singleton that caches the first loaded model for the duration of the process. Multiple callers in the same web request path (e.g., web.py search with type=all) still pay the model-loading cost multiple times via `get_model()`.
- **Foreign keys not enforced**: `get_conn()` in memory_db.py does not set `PRAGMA foreign_keys = ON`, so ON DELETE CASCADE constraints (on embeddings and fact_embeddings tables) are not enforced in production. Tests enable it explicitly for validation.
