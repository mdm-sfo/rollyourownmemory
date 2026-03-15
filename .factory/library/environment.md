# Environment

**What belongs here:** Required env vars, external dependencies, setup notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

- Python 3.12.3 (system)
- Venv at `.venv/` with sentence-transformers, numpy, httpx
- SQLite 3.x (system, with FTS5 support)
- No external services required — everything is local
- Optional: ollama for LLM-powered fact extraction (not required for this mission)
- Database file: `memory.db` in repo root (gitignored)
- State file: `state.json` in repo root (gitignored)
