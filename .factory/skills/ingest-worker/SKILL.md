---
name: ingest-worker
description: Worker for extending ingest.py with multi-tool session parsing
---

# Ingest Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Features that extend `src/ingest.py` with new source parsers, schema migrations for the messages table, or integration of new JSONL formats into the ingestion pipeline.

## Work Procedure

### 1. Read Context

- Read `src/ingest.py` thoroughly — understand all existing parsers, `discover_sources()`, `insert_records()`, `extract_text_content()`, `derive_project()`, and the state/offset tracking pattern.
- Read `src/memory_db.py` to understand the migration pattern (Migration 1–6 are examples).
- Read `tests/test_ingest.py` for existing test patterns.
- Read `.factory/library/` for format analysis and architectural notes.

### 2. Write Tests FIRST (Red Phase)

Before any implementation:
- Create test cases in `tests/test_ingest.py` for the new parser functions.
- Each test should use synthetic JSONL fixtures (write to tempfiles, parse, assert).
- Cover: normal parsing, skipped record types, content extraction, session_id propagation, project derivation, malformed input handling, timestamp conversion.
- Run `pytest tests/test_ingest.py -v` — tests should fail (functions don't exist yet).

### 3. Implement (Green Phase)

- Add schema migration in `memory_db.py` following the existing Migration N pattern (idempotent, using `ALTER TABLE ... ADD COLUMN` with try/except or column check).
- Add new parser functions in `ingest.py` following the pattern of existing parsers (accept filepath + offset, return records list + new_offset).
- Extend `discover_sources()` to find Factory and Codex JSONL files.
- Update `insert_records()` to include the new `source_tool` field.
- Update the main loop to dispatch to new parser functions.
- Each record dict must include `source_tool` key.

### 4. Verify (Green Phase)

- Run `pytest tests/test_ingest.py -v` — all new tests pass.
- Run `pytest tests/ -v` — all 257+ existing tests still pass.
- Run `python src/ingest.py --full` on real data and verify with SQL queries:
  - `SELECT source_tool, COUNT(*) FROM messages GROUP BY source_tool`
  - `SELECT DISTINCT project FROM messages WHERE source_tool='factory'`
  - `SELECT COUNT(*) FROM messages WHERE source_tool='codex'`

### 5. Manual Verification

- Verify idempotency: run ingest twice, check counts don't change.
- Verify the offset tracking: check `state.json` has entries for Factory/Codex files.

## Key Constraints

- Do NOT modify existing parser functions (parse_history_file, parse_project_jsonl, parse_interaction_jsonl).
- Do NOT modify schema.sql — migrations go in memory_db.py.
- The `extract_text_content()` function already handles Factory's content format — reuse it.
- For Factory project derivation, extend `derive_project()` to also recognize `sessions` directories (not just `projects`).
- Codex content blocks use `input_text`/`output_text` type instead of `text` — handle this in content extraction.
- Codex history timestamps are epoch SECONDS (not milliseconds like Claude Code).
- Skip `thinking` blocks (Factory) and `encrypted_content` (Codex) — these are unreadable.
- Skip Codex `developer` role messages (system instructions).

## Example Handoff

```json
{
  "salientSummary": "Extended ingest.py with Factory and Codex parsers. Added Migration 7 (source_tool column). Tests: 14 new test cases in test_ingest.py, all passing. Full ingest run: 324 Factory sessions → 2847 messages, 5 Codex sessions → 43 messages. All 271 tests pass.",
  "whatWasImplemented": "Added parse_factory_jsonl() and parse_codex_session_jsonl() parsers to ingest.py. Extended discover_sources() for ~/.factory/sessions/ and ~/.codex/sessions/. Added Migration 7 in memory_db.py (source_tool TEXT DEFAULT 'claude_code'). Updated insert_records() to persist source_tool. Extended derive_project() to handle 'sessions' directory paths. Added parse_codex_history() for ~/.codex/history.jsonl with epoch-second timestamp conversion.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "cd /home/matthewmurray/claude-memory && .venv/bin/python -m pytest tests/test_ingest.py -v", "exitCode": 0, "observation": "14 new tests passing alongside 8 existing"},
      {"command": "cd /home/matthewmurray/claude-memory && .venv/bin/python -m pytest tests/ -v", "exitCode": 0, "observation": "271 tests passing, 0 failures"},
      {"command": "cd /home/matthewmurray/claude-memory && .venv/bin/python src/ingest.py --full", "exitCode": 0, "observation": "Processed 340 sources, 15000 records parsed, 2890 new inserted"},
      {"command": "sqlite3 memory.db \"SELECT source_tool, COUNT(*) FROM messages GROUP BY source_tool\"", "exitCode": 0, "observation": "claude_code|12000, factory|2847, codex|43"}
    ],
    "interactiveChecks": [
      {"action": "Ran ingest.py twice to verify idempotency", "observed": "Second run: 0 new inserted"},
      {"action": "Checked state.json for Factory/Codex entries", "observed": "All Factory and Codex source files have offset entries"}
    ]
  },
  "tests": {
    "added": [
      {"file": "tests/test_ingest.py", "cases": [
        {"name": "test_parse_factory_messages", "verifies": "Factory user/assistant message extraction"},
        {"name": "test_factory_skips_non_message_types", "verifies": "session_start/end/todo_state skipped"},
        {"name": "test_factory_session_id_from_session_start", "verifies": "session_id propagation"},
        {"name": "test_factory_project_derivation", "verifies": "derive_project works with sessions/ paths"},
        {"name": "test_parse_codex_session_messages", "verifies": "Codex user/assistant extraction"},
        {"name": "test_codex_skips_developer_and_non_message", "verifies": "developer role and non-message types skipped"},
        {"name": "test_codex_both_phases_ingested", "verifies": "commentary + final_answer both included"},
        {"name": "test_codex_content_blocks", "verifies": "input_text/output_text extraction"},
        {"name": "test_codex_session_id_from_meta", "verifies": "session_id from session_meta"},
        {"name": "test_codex_project_from_cwd", "verifies": "project from payload.cwd"},
        {"name": "test_parse_codex_history", "verifies": "epoch-second timestamp conversion"},
        {"name": "test_malformed_lines_skipped", "verifies": "graceful handling of bad JSON"},
        {"name": "test_source_tool_tagging", "verifies": "records have correct source_tool value"},
        {"name": "test_insert_records_includes_source_tool", "verifies": "source_tool persisted to DB"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- The existing `derive_project()` cannot be extended to handle `sessions` paths without breaking existing behavior
- Real Factory/Codex JSONL files have a format significantly different from what's documented in `.factory/library/`
- The `insert_records()` dedup index needs changes that could affect existing data integrity
