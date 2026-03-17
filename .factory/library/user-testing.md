# User Testing

**What belongs here:** Testing surface findings, validation approach, resource cost classification.

---

## Validation Surface

- **CLI commands**: `distill.py backfill_embeddings`, `embed.py build --reembed`, `distill.py run --embed-model`, `distill.py run --no-segment`
- **Web API**: `/api/search?type=all` (check for `semantic_facts` key), `/api/ask` (verify semantic context)
- **MCP tools**: `memory_search_facts_semantic` tool registration and output format
- **Frontend**: `static/app.js` rendering of semantic fact cards
- **Unit tests**: pytest test suite (166 existing + new tests)

## Validation Concurrency

Machine: 20 cores, 121GB RAM, ~27GB used at baseline. Headroom: ~66GB (70% = ~46GB usable).
- **pytest**: Single process, ~200MB. Max concurrent: 5 (but 1 is sufficient)
- **CLI verification**: Minimal resources. Max concurrent: 5
- **Web API curl**: Negligible. Max concurrent: 5

All validation for this mission is CLI/API/unit-test based. No browser testing needed.

## Flow Validator Guidance: CLI/API/Unit-Test

**Testing tools:** All assertions are verified via:
1. **pytest** — run existing and new tests with `cd /home/matthewmurray/claude-memory && source .venv/bin/activate && pytest tests/ -v`
2. **CLI commands** — run Python scripts directly via `.venv/bin/python` or `source .venv/bin/activate && python3`
3. **curl** — hit web API endpoints at `http://localhost:8585`
4. **Code inspection** — use Grep/Read tools to verify code patterns

**Isolation rules:**
- Tests use in-memory SQLite databases (no shared state between subagents)
- CLI commands operate read-only or on temporary files
- Web API is read-only for search endpoints
- No subagent should modify production `memory.db`
- Each subagent can run pytest, curl, and CLI commands independently without conflict

**Environment:**
- Python venv: `/home/matthewmurray/claude-memory/.venv`
- Web service: `http://localhost:8585` (already running)
- Project root: `/home/matthewmurray/claude-memory`
- Mission dir: `/home/matthewmurray/.factory/missions/31299186-2caf-4e1d-ae2b-08091e3ea3f5`

## Flow Validator Guidance: Multi-Tool Ingestion

**Milestone:** multi-tool-ingestion
**Testing surface:** CLI/pytest/sqlite3 — No services needed (batch ETL)

**How to test assertions:**
1. **Schema assertions (VAL-SCHEMA-*)**: Run `PRAGMA table_info(messages)` on production `memory.db` to verify column exists. Run `SELECT COUNT(*) FROM messages WHERE source_tool IS NULL` to verify defaults. Test idempotency by calling migrate_schema() in Python.
2. **Factory assertions (VAL-FACTORY-*)**: Run specific pytest tests with `-k factory` filter. Also query production DB: `SELECT DISTINCT source_tool, COUNT(*) FROM messages WHERE source_tool='factory' GROUP BY source_tool`. Check project derivation with `SELECT DISTINCT project FROM messages WHERE source_tool='factory'`.
3. **Codex assertions (VAL-CODEX-*)**: Run specific pytest tests with `-k codex` filter. Query production DB for codex records. Check `~/.codex/sessions/` for real data.
4. **Integration assertions (VAL-INT-*)**: Run full test suite `pytest tests/ -v`. Test idempotency by running ingest twice. Check discover_sources output.
5. **Cross-area assertions (VAL-CROSS-*)**: Query production DB after ingest for records from all three tools.

**Database access:**
- Production DB: `/home/matthewmurray/claude-memory/memory.db` (read-only queries safe)
- For write tests, use temporary copies or in-memory DBs
- sqlite3 binary available: `sqlite3 /home/matthewmurray/claude-memory/memory.db`

**Real data locations:**
- Factory sessions: `~/.factory/sessions/` (324 JSONL files)
- Codex sessions: `~/.codex/sessions/` (5 JSONL files)
- Codex history: `~/.codex/history.jsonl`

**Isolation:**
- Read-only queries to production DB are safe from any subagent
- pytest uses in-memory DBs (no conflicts between subagents)
- No subagent should run `ingest.py` against production DB (it was already run)
