---
name: memory-worker
description: Implements features for the rollyourownmemory Python/SQLite project following a detailed spec doc.
---

# Memory Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for all implementation features in this mission: schema changes, Python module modifications, frontend JS changes, and test additions for the claude-memory project.

## Work Procedure

1. **Read the authoritative spec.** Open and read `/home/matthewmurray/Resilio Sync/wormhole/_temp/lossless-claw-improvements (3).md` to get the full implementation details for your assigned feature. Also read `AGENTS.md` for boundary constraints and known nits to fix.

2. **Read the current source files** you will modify. Understand the existing code before making changes.

3. **Write tests first (TDD).** For each new function or behavior:
   - Add test cases to the appropriate existing test file (`tests/test_memory_db.py`, `tests/test_web.py`, `tests/test_mcp_server.py`) or create a new test file if needed.
   - Follow the existing test patterns: use the `db` fixture from conftest.py for in-memory SQLite tests.
   - Tests must cover: happy path, edge cases (empty data, missing tables), and the specific behaviors listed in the feature's `expectedBehavior`.

4. **Implement the changes.** Follow the spec's code snippets precisely, with these corrections:
   - Use `FAISS_INDEX_PATH` and `FAISS_IDS_PATH` constants (not hardcoded paths) for `--reembed`.
   - Ensure all new functions have type hints on parameters and return types.
   - Follow the existing import pattern: `try: from src.X except ImportError: from X`.

5. **Run the full test suite.** Execute: `cd /home/matthewmurray/claude-memory && source .venv/bin/activate && pytest tests/ -v`
   - All 166 existing tests MUST pass.
   - All new tests MUST pass.
   - If any test fails, fix the issue before proceeding.

6. **Verify Python files parse correctly:**
   ```bash
   cd /home/matthewmurray/claude-memory && for f in src/distill.py src/embed.py src/mcp_server.py src/memory_db.py src/web.py; do
     python3 -c "import ast; ast.parse(open('$f').read()); print('$f: OK')"
   done
   ```

7. **Verify schema.sql is valid** (if modified):
   ```bash
   sqlite3 :memory: < /home/matthewmurray/claude-memory/schema.sql
   ```

8. **Verify CLI commands work** (if added):
   - `cd /home/matthewmurray/claude-memory && source .venv/bin/activate && python3 src/distill.py backfill_embeddings --help`
   - `cd /home/matthewmurray/claude-memory && source .venv/bin/activate && python3 src/embed.py build --help` (check for --reembed)

9. **Commit** with a descriptive message matching the spec's suggested commit messages.

## Example Handoff

```json
{
  "salientSummary": "Implemented fact_embeddings table (schema + migration 6), persisted embeddings in store_facts(), optimized _load_existing_fact_embeddings() to load from DB, added backfill_embeddings CLI command. Ran pytest (174 passed, 0 failed). Schema validates. AST parse OK for all files.",
  "whatWasImplemented": "Added fact_embeddings table to schema.sql and migration 6 to memory_db.py. Modified store_facts() in distill.py to persist the dedup embedding into fact_embeddings after successful INSERT. Rewrote _load_existing_fact_embeddings() to load from fact_embeddings table with fallback encoding. Added backfill_fact_embeddings() function and CLI subcommand. Added 8 new tests in test_memory_db.py covering migration, persistence, loading, and backfill.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "cd /home/matthewmurray/claude-memory && source .venv/bin/activate && pytest tests/ -v", "exitCode": 0, "observation": "174 passed in 31.2s"},
      {"command": "sqlite3 :memory: < schema.sql", "exitCode": 0, "observation": "Schema valid"},
      {"command": "python3 -c \"import ast; ast.parse(open('src/memory_db.py').read())\"", "exitCode": 0, "observation": "Parses OK"}
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": [
      {"file": "tests/test_memory_db.py", "cases": [
        {"name": "test_migration_6_creates_fact_embeddings", "verifies": "fact_embeddings table exists after migration"},
        {"name": "test_fact_embeddings_cascade_delete", "verifies": "deleting fact cascades to fact_embeddings"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- If the spec's code snippets conflict with the actual current codebase state (e.g., function signatures changed)
- If sentence-transformers or numpy imports fail in the venv
- If existing tests break and the root cause is unclear
- If the feature depends on changes from a previous feature that hasn't been implemented yet
