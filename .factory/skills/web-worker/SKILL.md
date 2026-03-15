---
name: web-worker
description: Implements FastAPI backend endpoints and vanilla HTML/CSS/JS frontend for the memory web UI
---

# Web Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features that involve:
- FastAPI API endpoint implementation
- HTML/CSS/JS frontend pages and components
- Integration between the web UI and the existing memory database
- Wiring up search, curation, and editor functionality

## Work Procedure

### 1. Read Context

- Read `AGENTS.md` for boundaries, conventions, and architecture decisions.
- Read the feature description carefully — note preconditions, expectedBehavior, and verificationSteps.
- Read `.factory/library/` files for any relevant knowledge.
- Read `.factory/services.yaml` for commands and service definitions.
- If the feature involves existing code, read the relevant source files (e.g., `src/memory_db.py`, `src/embed.py`).

**CRITICAL ADAPTATION WARNING:** This codebase has been through 2 prior missions. The code in `src/` may differ from any examples in library files. ALWAYS read the actual source files before writing code that imports from them. Check function signatures, imports, and module structure.

### 2. Write Tests First (Red)

- Create or update `tests/test_web.py` with test cases for the feature.
- Use FastAPI's `TestClient` with an in-memory SQLite database.
- Tests must cover: happy path, error cases, edge cases from expectedBehavior.
- Run tests and confirm they FAIL (red): `.venv/bin/python -m pytest tests/test_web.py -v`

### 3. Implement Backend

- API endpoints go in `src/web.py`.
- Use `memory_db.get_conn()` for database access.
- All endpoints return JSON. Errors return `{"error": "message"}` with appropriate status codes.
- Wrap FTS5 queries in try/except sqlite3.OperationalError.
- For semantic search, import from `src/embed.py` (check actual function signatures first).
- For LLM synthesis, use httpx to call ollama at localhost:11434.

### 4. Implement Frontend

- HTML/CSS/JS files go in `static/` directory.
- Use vanilla JS (ES6+). No frameworks, no CDN dependencies.
- All user-provided content must be HTML-escaped before rendering (XSS prevention).
- Use `fetch()` for API calls. Handle loading states and errors.
- CSS should be clean and modern. Dark mode is a nice-to-have but not required.

### 5. Run Tests (Green)

- Run: `.venv/bin/python -m pytest tests/ -v`
- ALL tests must pass (not just new ones).

### 6. Manual Verification

- Start the server: `.venv/bin/python -m uvicorn src.web:app --host 0.0.0.0 --port 8585 &`
- Use curl to verify API endpoints respond correctly.
- Check that static files are served (curl the HTML page).
- Stop the server: `lsof -ti :8585 | xargs kill`
- Each manual check = one `interactiveChecks` entry.

### 7. Cleanup

- Ensure no processes left running on port 8585.
- Run final test suite: `.venv/bin/python -m pytest tests/ -v`

## Example Handoff

```json
{
  "salientSummary": "Implemented /api/search endpoint with FTS + semantic + facts search. Created search page with mode toggle (Search/Ask). Ran `pytest tests/ -v` (14 passing). Verified via curl: FTS returns results in <200ms, semantic search falls back gracefully when FAISS unavailable.",
  "whatWasImplemented": "Added GET /api/search endpoint that queries messages_fts, facts_fts, and optionally FAISS similarity search. Returns JSON with sections: {messages: [...], facts: [...], sessions: [...]}. Created static/index.html with search bar, mode toggle tabs, and results rendering. CSS in static/style.css. JS in static/app.js handles fetch, streaming SSE for ask mode, and result card rendering with HTML escaping.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": ".venv/bin/python -m pytest tests/ -v", "exitCode": 0, "observation": "14 tests passed including 6 new search API tests"},
      {"command": "curl -s 'http://localhost:8585/api/search?q=kalshi' | python3 -m json.tool | head -20", "exitCode": 0, "observation": "Returns JSON with messages and facts arrays, 8 results total"},
      {"command": "curl -w '%{time_total}' -o /dev/null -s 'http://localhost:8585/api/search?q=kalshi'", "exitCode": 0, "observation": "Response time: 0.087s (under 500ms requirement)"},
      {"command": "curl -s http://localhost:8585/ | head -5", "exitCode": 0, "observation": "Returns HTML with search bar and mode toggle"}
    ],
    "interactiveChecks": [
      {"action": "curl /api/search with empty query", "observed": "Returns 200 with empty arrays and helpful message"},
      {"action": "curl /api/search with FTS syntax error", "observed": "Returns 200 with FTS error caught, returns empty results gracefully"}
    ]
  },
  "tests": {
    "added": [
      {
        "file": "tests/test_web.py",
        "cases": [
          {"name": "test_search_fts_returns_messages", "verifies": "FTS search returns message results"},
          {"name": "test_search_returns_facts", "verifies": "Search includes fact results"},
          {"name": "test_search_empty_query", "verifies": "Empty query returns helpful empty state"},
          {"name": "test_search_fts_syntax_error", "verifies": "Invalid FTS syntax handled gracefully"},
          {"name": "test_health_endpoint", "verifies": "Health check returns DB status"},
          {"name": "test_static_files_served", "verifies": "HTML page loads from static directory"}
        ]
      }
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Database schema doesn't match expected structure (missing tables/columns)
- Port 8585 is occupied by another process that can't be killed
- The embed.py or inject.py modules have incompatible interfaces
- Feature requires modifying existing src/ files (off-limits per AGENTS.md)
