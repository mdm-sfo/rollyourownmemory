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
- **Migration system**: `migrate_schema()` in memory_db.py applies idempotent migrations (1-5 exist, adding 6)
- **FTS5 sync**: Triggers keep FTS tables in sync with source tables automatically
