# User Testing

## Validation Surface

**Primary surface:** Web browser at http://localhost:8585
**Tool:** agent-browser (available at ~/.factory/bin/agent-browser)
**Setup:** Start FastAPI server via `cd /home/matthewmurray/claude-memory && .venv/bin/python -m uvicorn src.web:app --host 0.0.0.0 --port 8585`
**Database:** Uses production memory.db which has real data (500+ facts, 13k+ messages, 400+ sessions)
**LLM:** ollama at localhost:11434 with llama3.3:70b (required for Ask mode testing)

## Validation Concurrency

**Machine:** 121GB RAM, 20 CPUs, ~95GB available at baseline
**Per agent-browser instance:** ~300MB RAM
**FastAPI server:** ~50MB RAM
**Max concurrent validators:** 5 (70% headroom: 66GB / ~350MB per instance = easily 5+)

## Testing Notes

- The memory.db has real user data — searches for "kalshi", "tailscale", "JWT" should return results
- FAISS index (memory.faiss) exists for semantic search
- ~/.claude/CLAUDE.md exists and has real content
- inject.py --stdout produces real output
- ollama should be running for Ask mode tests; if not, verify graceful degradation

## Known Frictions

- **SPA click handling**: Playwright's click command times out on SPA elements that use addEventListener (fact cards, session cards) because it waits for navigation that never happens. Workaround: use `eval` to trigger `.click()` via JavaScript on data-attribute elements.
- **Bundled search timing**: The /api/search endpoint bundles FTS + semantic search in one call. FTS takes ~3.5ms but semantic search (sentence-transformers inference) takes ~1.5s, making total response ~1.6s. No parameter to request FTS-only results.
- **LLM first-token latency**: The 70B ollama model takes ~15s before first token. The UI shows a "Thinking..." spinner which provides adequate feedback.

## Flow Validator Guidance: browser

**Surface:** Web browser at http://localhost:8585
**Tool:** agent-browser (invoke via `Skill` tool with name `agent-browser`)
**Session naming:** Use `--session "<worker-session-id>__<group-id>"` pattern

### Isolation Rules (curation-and-context milestone)
- **Fact mutations** (edit, delete, confidence changes) are grouped into ONE subagent to avoid conflicts
- **CLAUDE.md mutations** are in a separate subagent — it backs up and restores the file
- **Context preview** is read-only and safe to run in parallel
- Each validator uses its own agent-browser session
- All fact CRUD operations (VAL-FACTS-005/006/007, VAL-CROSS-003) must be in the same subagent

### Testing Approach
- Use agent-browser to navigate to pages, fill search forms, click results
- Take screenshots as evidence for visual assertions
- Use curl directly for API-level assertions (timing, response format, headers)
- For streaming assertions (SSE), use curl with timeout to verify progressive response
- Save evidence screenshots to the designated evidence directory

### Known Data Points for Testing
- Search for "kalshi" returns messages, facts, and sessions
- Fact ID 1 exists (category: context)
- Sessions exist with real data
- ollama is available at localhost:11434 with llama3.3:70b

## Flow Validator Guidance: curl

**Surface:** API endpoints at http://localhost:8585/api/*
**Tool:** curl (direct command execution)

### Testing Approach
- Use curl with `-sf` for success checks, `-w '%{time_total}'` for timing
- Parse JSON responses with python3 -m json.tool or inline python
- Check status codes, response structure, error handling
- No isolation concerns — all read-only API calls
