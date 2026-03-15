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
