# Roll Your Own Memory

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![SQLite](https://img.shields.io/badge/storage-SQLite-003B57.svg)](https://sqlite.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-brightgreen.svg)](https://modelcontextprotocol.io)
[![100% Local](https://img.shields.io/badge/privacy-100%25%20local-success.svg)](#privacy)

> **"Bro, we literally fixed this exact same bug two weeks ago. Why don't you remember that?"**

That's the problem. Every Claude Code session starts from zero. It doesn't know your projects, your preferences, or that you debugged this identical stack trace last Tuesday. You re-explain context constantly. You lose decisions. You repeat yourself.

This fixes that.

**Roll Your Own Memory** gives Claude Code persistent, searchable memory across every session — fully local, fully private, no cloud APIs.

![Demo](demo.gif)

### How it works

- **Passive recall** — a memory context file auto-injects your key facts, recent sessions, and tech stack into every session via `CLAUDE.md`
- **Active recall** — search your full conversation history by keyword or meaning, mid-session, via MCP tools or slash commands
- **Knowledge accumulation** — facts, preferences, and decisions are extracted from every conversation and build up over time

No external services. No API keys. Just SQLite, local embeddings, and an optional local LLM.

## What 8 Weeks of Memory Looks Like

After ~2 months of normal Claude Code usage, the system has accumulated:

| Metric | Value |
|--------|-------|
| Conversations indexed | 13,000+ messages across 400+ sessions |
| Vector embeddings | 13,000+ (every message searchable by meaning) |
| Facts extracted | 670+ (preferences, decisions, learnings, context) |
| Entities tracked | 330+ (libraries, tools, services, languages) |
| Machines covered | 3 (primary dev, cloud server, secondary) |
| Projects spanned | 14 |
| Database size | 40 MB |

The entity graph alone tells you things you might not track yourself:

```
Languages:      python(85x), bash(42x), javascript(6x)
Platforms:       github(107x), aws(16x), vercel(21x)
AI Services:    claude(318x), perplexity(47x), gpt(28x)
Infrastructure: tailscale(33x), systemd(23x), docker(5x)
Tools:          git(26x), playwright(19x), pip(15x), tmux(11x)
Databases:      sqlite(10x), postgres(6x)
Protocols:      ssh(77x), http(76x), websocket(20x)
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Claude Code Session                    │
│                                                          │
│  CLAUDE.md ──▶ @memory-context.md (auto-generated)      │
│                                                          │
│  MCP Tools:  memory_search, memory_semantic_search,      │
│              memory_search_facts, memory_add_fact,        │
│              memory_get_session, memory_list_sessions,    │
│              memory_find_entity                           │
│                                                          │
│  Slash Commands: /recall, /sessions, /session, /facts    │
└──────────────┬───────────────────────────┬───────────────┘
               │                           │
               ▼                           ▼
┌──────────────────────┐    ┌──────────────────────────────┐
│     MCP Server       │    │      memory-context.md       │
│   (mcp_server.py)    │    │   (auto-generated every      │
│   stdio transport    │    │    30 min by inject.py)       │
└──────────┬───────────┘    └──────────────┬───────────────┘
           │                               │
           ▼                               ▼
┌──────────────────────────────────────────────────────────┐
│                      memory.db (SQLite)                   │
│                                                           │
│  messages     — raw conversation messages + FTS5 index    │
│  embeddings   — sentence-transformer vectors (float32)    │
│  facts        — extracted preferences, decisions, etc.    │
│  entities     — tools, libraries, services mentioned      │
│  entity_mentions — links entities to messages/sessions    │
└──────────┬────────────┬────────────┬─────────────────────┘
           │            │            │
           ▼            ▼            ▼
┌──────────────┐ ┌────────────┐ ┌──────────────┐
│  ingest.py   │ │  embed.py  │ │  distill.py  │
│  (ETL from   │ │ (sentence- │ │ (LLM fact    │
│  JSONL logs) │ │ transformers│ │  extraction) │
│  every 15min │ │  hourly)   │ │   hourly)    │
└──────────────┘ └────────────┘ └──────────────┘
```

## What's In the Box

| File | Purpose |
|------|---------|
| `schema.sql` | SQLite schema: messages, embeddings, facts, entities with FTS5 indexes |
| `ingest.py` | ETL pipeline: reads Claude Code JSONL logs → SQLite. Incremental via byte-offset cursors. |
| `embed.py` | Generates sentence-transformer embeddings for semantic search. |
| `distill.py` | Extracts structured facts from conversations using regex heuristics + local LLM (ollama). |
| `entities.py` | Identifies tools, libraries, languages, platforms mentioned across conversations. |
| `inject.py` | Generates `memory-context.md` for passive injection into CLAUDE.md. Project-aware via `$PWD`. |
| `curate.py` | Interactive fact review, hand-curation, import/export. |
| `claude-recall` | CLI search tool: keyword, semantic, sessions, facts. Backward-compatible with bare queries. |
| `mcp_server.py` | MCP server exposing all memory functions as tools Claude Code can call directly. |

## Setup

### Prerequisites

- Python 3.10+
- [Claude Code](https://code.claude.com) installed
- [ollama](https://ollama.com) (optional, for LLM-powered fact extraction)
- GPU recommended for embeddings (works on CPU, just slower)

### 1. Clone and install

```bash
git clone https://github.com/mdm-sfo/rollyourownmemory.git
cd rollyourownmemory

python3 -m venv .venv
.venv/bin/pip install sentence-transformers numpy httpx
```

### 2. Initialize the database

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('memory.db')
conn.executescript(open('schema.sql').read())
conn.close()
print('Database initialized')
"
```

### 3. Run the initial ingest

```bash
python3 ingest.py
```

This reads your Claude Code conversation logs from:
- `~/.claude/history.jsonl` — user prompts with metadata
- `~/.claude/projects/*/` — full session transcripts (user + assistant)

The ingest is incremental — it tracks byte offsets in `state.json` so re-runs only process new data.

### 4. Generate embeddings

```bash
.venv/bin/python embed.py build
```

This creates a vector embedding for every message using `all-MiniLM-L6-v2` (384 dimensions). On a GPU, ~13K messages take about 2 minutes. On CPU, ~10 minutes.

### 5. Extract facts (optional but recommended)

**Heuristic only (no LLM needed):**
```bash
python3 distill.py run
```

**With local LLM (much higher quality):**
```bash
# Install ollama: https://ollama.com
ollama pull llama3.2          # 3B, fast, decent quality
# OR for much better results if you have the RAM:
ollama pull llama3.3:70b      # 70B, needs ~50GB RAM, excellent quality

.venv/bin/python distill.py run --llm
```

### 6. Extract entities

```bash
python3 entities.py run
```

### 7. Set up the MCP server

Add to `~/.claude/settings.local.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "/path/to/rollyourownmemory/.venv/bin/python",
      "args": ["/path/to/rollyourownmemory/mcp_server.py"]
    }
  }
}
```

Restart Claude Code. The memory tools are now available in every session.

### 8. Set up passive context injection

Add to your `~/.claude/CLAUDE.md`:

```markdown
## Memory

You have access to memory tools (`memory_search`, `memory_semantic_search`, `memory_search_facts`, etc.) that search my full conversation history. Use them proactively when:
- I reference past work or decisions vaguely
- A task might benefit from knowing what I've done before
- I ask you to remember something (use `memory_add_fact`)

@memory-context.md
```

Generate the initial context file:

```bash
.venv/bin/python inject.py -o ~/.claude/memory-context.md
```

### 9. Set up cron (keeps everything fresh automatically)

```bash
crontab -e
```

Add:

```cron
# Ingest new conversations every 15 minutes (uses stdlib only, no venv needed)
*/15 * * * * python3 /path/to/rollyourownmemory/ingest.py --quiet >> /path/to/rollyourownmemory/ingest.log 2>&1

# Embed, distill, and extract entities hourly
0 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/embed.py build >> /path/to/rollyourownmemory/ingest.log 2>&1
15 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/distill.py run >> /path/to/rollyourownmemory/ingest.log 2>&1
15 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/entities.py run >> /path/to/rollyourownmemory/ingest.log 2>&1

# Refresh context injection file every 30 minutes
*/30 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/inject.py --no-detect -o ~/.claude/memory-context.md 2>>/path/to/rollyourownmemory/ingest.log
```

If you want LLM-powered distillation on the cron, change the `distill.py` line to include `--llm`.

## Usage

### MCP Tools (automatic, in-session)

Claude Code calls these automatically when relevant. You can also ask directly. Here are real scenarios where memory saves you time:

**Fix it the same way as last time:**
> *"We had this exact CORS error last week on the dashboard project. Look up how we fixed it and do the same thing here."*
>
> Claude searches your memory, finds the session where you resolved it, sees you added specific middleware config, and applies the same fix — no re-debugging.

**Don't lose architectural decisions:**
> *"What did we decide about the auth architecture? I don't want to re-hash this."*
>
> Claude pulls up the extracted fact: "Decision: using JWT with refresh tokens, storing in httpOnly cookies, 15-min access / 7-day refresh." You pick up where you left off.

**Stop repeating your preferences:**
> *"Remember that I prefer Postgres over MySQL for new projects."*
>
> Claude stores this as a fact with confidence 1.0. Every future session where database selection comes up, it already knows.

**Recover context after stepping away:**
> *"What was I working on yesterday? I had a session going about the payment integration."*
>
> Claude lists your recent sessions, finds the one about Stripe webhooks, and summarizes where you left off — including the file you were editing and the test that was still failing.

**Reuse code patterns across projects:**
> *"I built a retry wrapper with exponential backoff in another project a few weeks ago. Find it."*
>
> Semantic search finds the session even if you don't remember which project, what you called it, or the exact words you used.

**Know your own stack:**
> *"What testing libraries have I actually been using over the last eight weeks? I want to standardize."*
>
> Claude queries your entity graph: "pytest mentioned 45x, playwright 20x, jest 3x" — decisions based on your actual usage, not guesswork.

### Slash Commands (manual, in-session)

```
/recall websocket debugging     # keyword + semantic search
/sessions                        # list recent sessions
/session 98a4a724               # view full conversation thread
/facts authentication            # search extracted facts
/curate                          # review and curate facts
```

### CLI (outside Claude Code)

```bash
# Keyword search
./claude-recall docker networking

# Semantic search (finds by meaning, not just keywords)
.venv/bin/python claude-recall search "that time the deploy script broke" --semantic

# List recent sessions
./claude-recall sessions --limit 10

# View a full conversation thread
./claude-recall session a1b2c3d4

# Search extracted facts
./claude-recall facts database --category decision

# Generate project-specific context
.venv/bin/python inject.py --project myproject
```

### Fact Curation

The system auto-extracts facts, but hand-curated facts are the highest value:

```bash
# Interactive review of auto-extracted facts
python3 curate.py review

# Import facts from a markdown file
cp curated-facts.example.md curated-facts.md
# Edit curated-facts.md with your facts, then:
python3 curate.py import curated-facts.md

# Export high-confidence facts
python3 curate.py export

# View stats
python3 curate.py stats
```

## Project-Aware Context

`inject.py` auto-detects the current project from `$PWD` and filters the context accordingly. When you run Claude Code from `~/myproject/`, the memory context emphasizes facts and sessions related to that project.

To add your own projects, edit the `PROJECT_ALIASES` dict in `inject.py`:

```python
PROJECT_ALIASES = {
    "myproject": "myproject",
    "my-other-project": "other",
}
```

## Multi-Machine Support

If you work across multiple machines:

1. **Sync conversation logs** to one machine (rsync, Resilio Sync, Syncthing, etc.)
2. Run the ingest/embed/distill pipeline on that machine
3. The MCP server runs on the machine with the database

The `ingest.py` script supports custom source directories. See `discover_sources()` for the paths it checks.

## Schema

```sql
messages        — id, source_file, session_id, project, role, content, timestamp, machine
embeddings      — message_id, embedding (float32 blob), model
facts           — id, session_id, project, fact, category, confidence, source_message_id
entities        — id, name, entity_type, first_seen, last_seen, mention_count
entity_mentions — id, entity_id, message_id, session_id, timestamp
```

FTS5 indexes on `messages.content` and `facts.fact` for fast keyword search. Porter stemming enabled.

## How the Pieces Fit Together

1. **You use Claude Code normally.** Conversations are saved as JSONL by Claude Code itself.
2. **`ingest.py`** (cron, every 15 min) reads new JSONL data into SQLite.
3. **`embed.py`** (cron, hourly) generates vector embeddings for new messages.
4. **`distill.py`** (cron, hourly) extracts facts from new sessions using regex + optionally a local LLM.
5. **`entities.py`** (cron, hourly) identifies tools/libraries/services mentioned.
6. **`inject.py`** (cron, every 30 min) generates `memory-context.md` with top facts, recent sessions, and active tech stack.
7. **`CLAUDE.md`** includes `@memory-context.md`, so every new session starts with context.
8. **`mcp_server.py`** lets Claude query the database directly mid-session for deeper recall.

## Configuration

### Embedding Model

Default: `all-MiniLM-L6-v2` (fast, 384 dimensions, ~90MB). To use a different model:

```bash
.venv/bin/python embed.py build --model all-mpnet-base-v2
```

Note: changing models requires re-embedding all messages.

### LLM for Distillation

Default: `llama3.2` (3B). For higher quality extraction with more RAM:

```bash
ollama pull llama3.3:70b
```

Then edit `distill.py` and change the model name in `extract_facts_llm()`.

### Context Budget

`inject.py` defaults to ~2000 tokens (~8KB) of context. Adjust with `--max-tokens`:

```bash
.venv/bin/python inject.py --max-tokens 3000 -o ~/.claude/memory-context.md
```

## Maintenance

**Monthly (~5 min):**
- Run `python3 curate.py review` to approve/reject auto-extracted facts
- Check `ingest.log` for errors, truncate if large

**Quarterly (~15 min):**
- Review and update your `CLAUDE.md` files as projects evolve
- Update `PROJECT_ALIASES` in `inject.py` for new projects
- Update `KNOWN_ENTITIES` in `entities.py` for new tools you've adopted

**The single highest-value maintenance task** is occasionally hand-curating 10-20 facts in `curated-facts.md` and running `python3 curate.py import curated-facts.md`. These confidence-1.0 facts always surface first in context injection.

## Privacy

- **Everything is local.** No data is sent to any external service.
- Embeddings are generated locally via sentence-transformers.
- Fact extraction uses a local LLM (ollama) or regex heuristics.
- The database, embeddings, and state files are in `.gitignore`.
- The MCP server communicates via stdio (no network port).

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — free to use, share, and adapt for non-commercial purposes with attribution.
