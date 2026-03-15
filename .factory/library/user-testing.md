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
