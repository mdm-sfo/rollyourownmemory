# Architecture

**What belongs here:** Architectural decisions, patterns, module relationships.

---

## Module Structure

- `src/ingest.py` — ETL: JSONL → SQLite. Standalone, stdlib only.
- `src/embed.py` — Sentence-transformer embeddings. Requires venv.
- `src/distill.py` — Fact extraction via regex + optional LLM. Requires venv for httpx.
- `src/entities.py` — Entity/tool/library extraction. Standalone.
- `src/inject.py` — Generates memory-context.md. Requires venv.
- `src/curate.py` — Interactive fact curation. Standalone.
- `src/mcp_server.py` — MCP server for Claude Code. Requires venv (mcp package).
- `src/memory_db.py` — (NEW) Shared query module. All DB access goes through here.
- `src/claude_recall.py` — (NEW) CLI module, importable entry point for pyproject.toml.
- `bin/claude-recall` — CLI script, thin wrapper around src/claude_recall.py.

## Database Schema

- `messages` — raw conversation messages with FTS5 index
- `embeddings` — float32 numpy blobs per message
- `facts` — extracted preferences/decisions with FTS5 index
- `entities` — tools/libraries/services mentioned
- `entity_mentions` — links entities to messages
- `processed_messages` — (NEW) tracks which processor has handled each message
- `facts.last_validated` — (NEW) timestamp for fact decay

## Key Patterns

- Every module defines `MEMORY_DIR = Path(__file__).parent.parent` and `DB_PATH = MEMORY_DIR / "memory.db"`
- FTS5 tables use content-sync triggers (see schema.sql)
- Argparse subcommands pattern for CLIs with multiple operations
- Lazy imports for heavy optional deps (sentence-transformers, faiss)
- Dual-import pattern in all consumer modules: `try: from src.memory_db import get_conn` / `except ImportError: from memory_db import get_conn` — supports both repo-root execution and src/-relative execution

## Build System

- Build backend is `setuptools.build_meta` (not `setuptools.backends._legacy:_Backend`)
- `.gitignore` includes `*.egg-info/` for pip editable installs
