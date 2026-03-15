# User Testing

**What belongs here:** Testing surface discovery, resource cost classification, validation approach.

---

## Validation Surface

This is a CLI/library project with no web UI. The validation surface is:

1. **CLI commands** — `bin/claude-recall` and `claude-recall` (pip-installed entry point)
2. **Python module imports** — verifying all modules import cleanly
3. **pytest** — unit and integration tests in `tests/`
4. **Script execution** — running `src/*.py` scripts with `--help` and basic operations

**Tools:** Direct CLI execution via shell commands. No agent-browser or tuistory needed.

## Validation Concurrency

**Surface: CLI/pytest**
- Max concurrent validators: **5**
- Rationale: 20 CPUs, 121 GB RAM. CLI tests are lightweight (~50 MB each). pytest runs are isolated. No resource concerns.

## Testing Approach

- Verify module imports: `python3 -c "from src.memory_db import get_conn"`
- Verify CLI: `claude-recall --help`, `bin/claude-recall --help`
- Verify pytest: `.venv/bin/python -m pytest tests/ -v`
- Verify FAISS operations with in-memory test data
- Verify schema migrations on fresh `:memory:` database
- Verify grep for bare `sqlite3.connect(` calls

## Flow Validator Guidance: CLI

**Surface:** CLI commands, Python imports, shell commands. No browser or TUI automation needed.

**Testing tool:** Direct shell execution via `Execute` tool. No special skills required.

**Isolation rules:**
- Each validator operates read-only on the codebase — no file modifications.
- All validators share the same venv at `.venv/` — this is safe since they only run read operations.
- Use in-memory SQLite (`:memory:`) for any database assertions to avoid touching `memory.db`.
- Working directory: `/home/matthewmurray/claude-memory`
- Python executable: `.venv/bin/python` (has all deps installed including pytest)
- The `claude-recall` entry point is installed in `.venv/bin/claude-recall`

**Key paths:**
- Schema file: `schema.sql`
- Source modules: `src/memory_db.py`, `src/inject.py`, `src/distill.py`, `src/curate.py`, `src/entities.py`, `src/embed.py`, `src/ingest.py`, `src/mcp_server.py`, `src/claude_recall.py`, `src/__init__.py`
- CLI script: `bin/claude-recall`
- pyproject.toml: `pyproject.toml`

**Evidence format:** Save text output files (command output, grep results) in the assigned evidence directory.
