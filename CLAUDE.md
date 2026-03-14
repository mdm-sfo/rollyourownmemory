# Claude Memory

Conversation memory system — ingests Claude Code JSONL logs into SQLite + FTS5 + vector embeddings.

## Architecture

- **Venv**: `.venv/` (sentence-transformers, numpy)
- **DB**: `memory.db` (SQLite with FTS5, embeddings, facts, entities)
- **Cron**: Ingest every 15 min, embed/distill/entities hourly, context injection every 30 min

## Key Files

| File | Purpose |
|------|---------|
| `ingest.py` | ETL: JSONL -> SQLite (uses tribunal venv, stdlib only) |
| `embed.py` | Generate sentence-transformer embeddings |
| `distill.py` | Extract structured facts from sessions |
| `entities.py` | Entity/tool/library extraction |
| `inject.py` | Generate `~/.claude/memory-context.md` |
| `claude-recall` | CLI search tool (FTS + semantic + facts + sessions) |

## Usage

```bash
claude-recall kalshi                          # keyword search
claude-recall search "query" --semantic       # vector search
claude-recall sessions                        # list recent sessions
claude-recall session <id>                    # view full session
claude-recall facts "query"                   # search extracted facts
claude-recall context --project kalshi        # generate context block
```
