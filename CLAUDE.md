<coding_guidelines>
# Claude Memory

Conversation memory system — ingests Claude Code JSONL logs into SQLite + FTS5 + vector embeddings.

## Architecture

- **Venv**: `.venv/` (sentence-transformers, numpy)
- **DB**: `memory.db` (SQLite with FTS5, embeddings, facts, entities)
- **Cron**: Ingest every 15 min, embed/distill/entities hourly, context injection every 30 min

## Key Files

| File | Purpose |
|------|---------|
| `src/ingest.py` | ETL: JSONL -> SQLite (uses tribunal venv, stdlib only) |
| `src/embed.py` | Generate sentence-transformer embeddings |
| `src/distill.py` | Extract structured facts from sessions |
| `src/entities.py` | Entity/tool/library extraction |
| `src/inject.py` | Generate `~/.claude/memory-context.md` |
| `src/curate.py` | Interactive fact review and curation |
| `src/mcp_server.py` | MCP server for Claude Code integration |
| `bin/claude-recall` | CLI search tool (FTS + semantic + facts + sessions) |

## Usage

```bash
bin/claude-recall kalshi                          # keyword search
bin/claude-recall search "query" --semantic       # vector search
bin/claude-recall sessions                        # list recent sessions
bin/claude-recall session <id>                    # view full session
bin/claude-recall facts "query"                   # search extracted facts
bin/claude-recall context --project kalshi        # generate context block
```
</coding_guidelines>
