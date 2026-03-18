# rollyourownmemory

Local conversation memory system — SQLite + FTS5 + sentence-transformer embeddings + local LLM fact extraction.

## Architecture

- **DB**: `memory.db` (SQLite with FTS5, vector embeddings, facts, entities)
- **Venv**: `.venv/` (sentence-transformers, numpy, httpx)
- **MCP server**: `src/mcp_server.py` — 12 tools for memory search, facts, entities, deep recall
- **Local LLM**: Ollama (model configurable via `MEMORY_LLM_MODEL` env var, default: `nemotron-3-super`)
- **Embeddings**: `all-MiniLM-L6-v2` (384-dim) default, `all-mpnet-base-v2` (768-dim) via registry in `embed.py`

## Key Files

| File | Purpose |
|------|---------|
| `src/config.py` | Centralized LLM model + ollama URL config (env var overrides) |
| `src/ingest.py` | ETL: JSONL → SQLite (Claude Code, Factory.ai, Codex CLI) |
| `src/embed.py` | Generate sentence-transformer embeddings (message + fact level) |
| `src/distill.py` | Extract structured facts via local LLM, with topic segmentation |
| `src/entities.py` | Entity/tool/library extraction |
| `src/inject.py` | Generate `memory-context.md` for agent startup |
| `src/mcp_server.py` | MCP server (stdio) — primary interface for all agents |
| `src/memory_db.py` | Database access layer |
| `src/web.py` | FastAPI web UI (`uvicorn src.web:app`) |
| `static/app.js` | Frontend |

## MCP Tools Available

| Tool | Purpose |
|------|---------|
| `memory_search` | Full-text keyword search across messages |
| `memory_semantic_search` | Vector similarity search by meaning |
| `memory_search_facts` | Search extracted facts (keyword, FTS5) |
| `memory_search_facts_semantic` | Search facts by meaning (vector similarity) |
| `memory_add_fact` | Store a new fact for long-term memory |
| `memory_get_session` | Retrieve full conversation thread |
| `memory_list_sessions` | List recent sessions with summaries |
| `memory_find_entity` | Find mentions of a tool/library/service |
| `memory_inspect` | Drill into a fact with full context |
| `memory_deep_recall` | Cross-session search with optional LLM synthesis |
| `memory_resume_context` | Resume context from a previous session |
| `memory_feedback` | Confidence calibration on facts |

## Conventions

- License: CC BY-NC 4.0
- Python 3.12+, aarch64 (DGX Spark) primary target
- LLM model is configured in `src/config.py` via `MEMORY_LLM_MODEL` env var (default: `nemotron-3-super`)
- Embedding model is `all-MiniLM-L6-v2` (384-dim) — do not swap without running the migration in embed.py

## Memory Context

See `memory-context.md` in the project root for auto-generated recent context (facts, decisions, entities). Updated every 30 minutes by `inject.py`.
