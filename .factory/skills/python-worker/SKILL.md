---
name: python-worker
description: Implements Python code changes, refactoring, and tests for the claude-memory codebase
---

# Python Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Any feature that involves Python code changes in the claude-memory codebase:
- Creating new modules (pyproject.toml, memory_db.py, claude_recall.py)
- Refactoring existing modules to use shared code
- Adding new features (FAISS, dedup, decay, migrations)
- Writing tests
- Updating documentation (README)

## Work Procedure

### 1. Understand the Feature

Read the feature description, preconditions, and expectedBehavior carefully. Identify ALL files that need to change.

Read `.factory/library/architecture.md` for module relationships. Read `AGENTS.md` for coding conventions and boundaries.

### 2. Read Affected Files

Read every file that the feature touches COMPLETELY. Do not skim. Understand the existing patterns, imports, and how the code flows.

For refactoring features: read BOTH the source files being modified AND the target shared module.

### 3. Plan Changes

Before writing code, list the specific changes needed:
- Which files to create
- Which files to modify (and what sections)
- What functions to add/change
- What imports to update

### 4. Write Tests First (when applicable)

For features that create testable modules (memory_db.py, tests/):
- Write test cases FIRST that describe the expected behavior
- Verify tests fail (red phase)
- Then implement to make them pass (green phase)

For refactoring features: the existing functionality IS the test — verify it still works after changes.

### 5. Implement

Make changes file by file. For each file:
- Make surgical edits, not full rewrites
- Preserve existing functionality
- Add type hints to all new functions
- Follow the coding conventions in AGENTS.md

### 6. Verify

Run verification steps specified in the feature:
- Run tests: `.venv/bin/python -m pytest tests/ -v` (when tests exist)
- Run import checks: `python3 -c "from src.memory_db import get_conn"` etc.
- Run CLI checks: `bin/claude-recall --help`, `python3 src/distill.py run --help`, etc.
- Run grep checks: verify no bare `sqlite3.connect(` outside memory_db.py
- Check that existing CLI --help commands still work for modified files

### 7. Commit

Use the EXACT commit message specified in the feature description. No modifications.

```bash
git add -A
git commit -m "<exact message from feature>"
```

## Example Handoff

```json
{
  "salientSummary": "Extracted shared memory_db.py module with get_conn(), search_fts(), search_facts_fts(), get_session_messages(), list_recent_sessions(), store_fact(). Refactored mcp_server.py, bin/claude-recall, inject.py, distill.py to import from memory_db. Verified all CLI --help commands still work and ran grep confirming zero bare sqlite3.connect calls outside memory_db.py.",
  "whatWasImplemented": "Created src/memory_db.py with 6 shared functions. Refactored 4 consumer files to use memory_db imports. Removed ~120 lines of duplicated query code. All connections now go through get_conn() with WAL + busy_timeout.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "python3 -c \"from src.memory_db import get_conn, search_fts, search_facts_fts\"", "exitCode": 0, "observation": "All imports successful"},
      {"command": "bin/claude-recall --help", "exitCode": 0, "observation": "Shows search, session, sessions, facts, context subcommands"},
      {"command": "python3 src/inject.py --help", "exitCode": 0, "observation": "Shows --project, --focus, --max-tokens, --output, --no-detect flags"},
      {"command": "python3 src/mcp_server.py --help 2>&1 || true", "exitCode": 0, "observation": "MCP server module loads without error"},
      {"command": "rg 'sqlite3\\.connect\\(' --type py -l", "exitCode": 0, "observation": "Only src/memory_db.py contains sqlite3.connect"},
      {"command": "git diff --stat", "exitCode": 0, "observation": "5 files changed, 180 insertions, 200 deletions"}
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": []
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- A file that should exist (from a precondition) doesn't exist
- Circular import detected that can't be resolved within the feature scope
- Schema migration breaks existing data patterns
- A dependency that should be installed is missing from the venv
- The existing code has bugs that affect the feature but aren't part of the feature's scope
