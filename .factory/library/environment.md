# Environment

**What belongs here:** Required env vars, external dependencies, setup notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

- Python 3.12.3 on Ubuntu (DGX Spark, 128GB unified memory, 20 cores)
- Virtual environment: `/home/matthewmurray/claude-memory/.venv`
- SQLite database (production): `/home/matthewmurray/claude-memory/memory.db`
- FAISS index: `/home/matthewmurray/claude-memory/memory.faiss` + `memory_ids.json`
- Ollama running at `http://localhost:11434` with `llama3.3:70b` loaded
- Sentence-transformers installed with `all-MiniLM-L6-v2` (384-dim, default)
- Optional: `all-mpnet-base-v2` (768-dim) available via sentence-transformers
