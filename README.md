# Roll Your Own Memory

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![SQLite](https://img.shields.io/badge/storage-SQLite-003B57.svg)](https://sqlite.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-brightgreen.svg)](https://modelcontextprotocol.io)
[![100% Local](https://img.shields.io/badge/privacy-100%25%20local-success.svg)](#privacy)

> **"Bro, we literally fixed this exact same bug two weeks ago. Why don't you remember that?"**

That's the problem. Every AI coding session starts from zero. It doesn't know your projects, your preferences, or that you debugged this identical stack trace last Tuesday. You re-explain context constantly. You lose decisions. You repeat yourself.

This fixes that.

**Roll Your Own Memory** gives your AI coding tools persistent, searchable memory across every session — fully local, fully private, no cloud APIs. Works with **Claude Code**, **Factory.ai (Droid)**, and **OpenAI Codex CLI**.

<p align="center">
  <img src="demo.gif" alt="Demo" width="600">
</p>

### How it works

- **Passive recall** — a memory context file auto-injects your key facts, recent sessions, and tech stack into every session via `CLAUDE.md`
- **Active recall** — search your full conversation history by keyword or meaning, mid-session, via MCP tools or slash commands
- **Knowledge accumulation** — facts, preferences, and decisions are extracted from every conversation and build up over time
- **Multi-tool support** — ingests conversations from Claude Code, Factory.ai, and Codex CLI into a single unified memory

No external services. No API keys. Just SQLite, local embeddings, and an optional local LLM.

## What 8 Weeks of Memory Looks Like

After ~2 months of normal Claude Code usage, the system has accumulated:

| Metric | Value |
|--------|-------|
| Conversations indexed | 13,000+ messages across 400+ sessions |
| Vector embeddings | 13,000+ (every message searchable by meaning) |
| Facts extracted | 670+ (preference, decision, learning, context, tool, pattern, error, solution) |
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
│              memory_search_facts,                         │
│              memory_search_facts_semantic,                │
│              memory_add_fact,                             │
│              memory_get_session, memory_list_sessions,    │
│              memory_find_entity, memory_inspect,          │
│              memory_deep_recall, memory_resume_context,   │
│              memory_feedback                              │
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
│  fact_embeddings — vectors for individual facts           │
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
| `src/ingest.py` | ETL pipeline: reads JSONL logs from Claude Code, Factory.ai, and Codex CLI → SQLite. Incremental via byte-offset cursors. Tracks `source_tool` per message. |
| `src/embed.py` | Generates sentence-transformer embeddings for semantic search. Model registry with short names (`minilm`, `mpnet`). `--reembed` flag to switch models. Dimension-mismatch safety for mixed-model databases. |
| `src/distill.py` | Extracts structured facts from conversations using regex heuristics + local LLM (ollama). Includes dedup (embedding-similarity deduplication), cross-project pattern detection, error/solution categorization, compressed_details extraction, conversation segmentation, and `backfill_embeddings` for existing facts. |
| `src/entities.py` | Identifies tools, libraries, languages, platforms mentioned across conversations. |
| `src/inject.py` | Generates `memory-context.md` for passive injection into CLAUDE.md. Project-aware via `$PWD`. |
| `src/curate.py` | Interactive fact review, hand-curation, import/export. |
| `bin/claude-recall` | CLI search tool: keyword, semantic, sessions, facts. Backward-compatible with bare queries. |
| `src/mcp_server.py` | MCP server exposing memory tools: search, facts, semantic fact search, inspect, deep recall, resume context, feedback. |
| `src/web.py` | FastAPI web UI: browser-based search (including semantic fact search in Ask mode), fact curation with project/source-tool filters, CLAUDE.md editor, context preview. |
| `static/` | Frontend assets (HTML, CSS, JS) for the web UI. |

## Setup

### Prerequisites

- Python 3.10+
- At least one supported AI coding tool:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (primary)
  - [Factory.ai / Droid](https://factory.ai) (supported)
  - [OpenAI Codex CLI](https://github.com/openai/codex) (supported)
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
python3 src/ingest.py
```

This reads conversation logs from all supported tools:
- **Claude Code**: `~/.claude/history.jsonl` and `~/.claude/projects/*/` session transcripts
- **Factory.ai**: `~/.factory/sessions/*/` session JSONLs
- **Codex CLI**: `~/.codex/sessions/` recursive session JSONLs and `~/.codex/history.jsonl`

Each message is tagged with a `source_tool` (`claude_code`, `factory`, or `codex`) for attribution. The ingest is incremental — it tracks byte offsets in `state.json` so re-runs only process new data.

### 4. Generate embeddings

```bash
.venv/bin/python src/embed.py build
```

This creates a vector embedding for every message using `all-MiniLM-L6-v2` (384 dimensions). On a GPU, ~13K messages take about 2 minutes. On CPU, ~10 minutes.

### 5. Extract facts (optional but recommended)

**Heuristic only (no LLM needed):**
```bash
python3 src/distill.py run
```

**With local LLM (much higher quality):**
```bash
# Install ollama: https://ollama.com
ollama pull llama3.3:70b      # 70B, needs ~50GB RAM, excellent quality (default)
# OR for a smaller model:
ollama pull llama3.2          # 3B, fast, decent quality

.venv/bin/python src/distill.py run --llm
# Or with a specific model:
.venv/bin/python src/distill.py run --llm --model llama3.2
```

### 6. Extract entities

```bash
python3 src/entities.py run
```

### 7. Set up the MCP server

Add to `~/.claude/settings.local.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "/path/to/rollyourownmemory/.venv/bin/python",
      "args": ["/path/to/rollyourownmemory/src/mcp_server.py"]
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
.venv/bin/python src/inject.py -o ~/.claude/memory-context.md
```

### 9. Set up cron (keeps everything fresh automatically)

```bash
crontab -e
```

Add:

```cron
# Ingest new conversations every 15 minutes (uses stdlib only, no venv needed)
*/15 * * * * python3 /path/to/rollyourownmemory/src/ingest.py --quiet >> /path/to/rollyourownmemory/ingest.log 2>&1

# Embed, distill, and extract entities hourly
0 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/src/embed.py build >> /path/to/rollyourownmemory/ingest.log 2>&1
15 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/src/distill.py run >> /path/to/rollyourownmemory/ingest.log 2>&1
15 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/src/entities.py run >> /path/to/rollyourownmemory/ingest.log 2>&1

# Refresh context injection file every 30 minutes
*/30 * * * * /path/to/rollyourownmemory/.venv/bin/python /path/to/rollyourownmemory/src/inject.py --no-detect -o ~/.claude/memory-context.md 2>>/path/to/rollyourownmemory/ingest.log
```

If you want LLM-powered distillation on the cron, change the `distill.py` line to include `--llm`.

### Hook-Based Automation (Recommended)

Claude Code hooks trigger the memory pipeline at exactly the right moments — no cron delay.
Add this to your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/rollyourownmemory/hooks/memory-hook.sh",
            "timeout": 10,
            "statusMessage": "Loading memory context..."
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/rollyourownmemory/hooks/memory-hook.sh",
            "timeout": 3
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/rollyourownmemory/hooks/memory-hook.sh",
            "timeout": 15,
            "statusMessage": "Saving memory before compaction..."
          }
        ]
      }
    ]
  }
}
```

Replace `/absolute/path/to/rollyourownmemory` with the actual path to your installation.

**What each hook does:**
- **SessionStart**: Runs `inject.py --stdout` and feeds memory context directly into Claude's context window via `additionalContext`. Claude starts every session already knowing your preferences and recent work.
- **SessionEnd**: Triggers `ingest.py` + `embed.py` in the background. Your conversation is in the database within seconds of closing, not at the next cron tick. Background processes (`&`) are essential — SessionEnd has a 1.5s default timeout.
- **PreCompact**: Runs `ingest.py` synchronously, then `distill.py` in the background. Facts are extracted from the full conversation before compaction strips detail.

**Note**: Hooks complement cron — keep your cron job as a safety net for edge cases where hooks don't fire (e.g., force-quit). The hook handles the latency-sensitive path; cron handles the durability path.

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

**Drill into compressed details:**
> *"How exactly did we configure the JWT refresh logic?"*
>
> Claude searches facts, finds "Decision: using JWT with refresh tokens" with compressed details listing "cookie config, rotation logic, logout invalidation". Claude calls `memory_inspect` to get the source message, then `memory_deep_recall` to synthesize the complete answer from all related context — delivering the exact configuration without you re-explaining anything.

**Find facts by meaning, not keywords:**
> *"What did we decide about how to handle errors gracefully?"*
>
> `memory_search_facts_semantic` finds facts about error handling, retry logic, and fallback strategies even if they don't contain the word "error" — using vector similarity on individual fact embeddings.

`memory_deep_recall` and Ask mode now combine both FTS and semantic search across facts and messages (with ID-based dedup), so you get comprehensive results from both keyword matches and meaning-based matches.

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
./bin/claude-recall docker networking

# Semantic search (finds by meaning, not just keywords)
.venv/bin/python bin/claude-recall search "that time the deploy script broke" --semantic

# List recent sessions
./bin/claude-recall sessions --limit 10

# View a full conversation thread
./bin/claude-recall session a1b2c3d4

# Search extracted facts
./bin/claude-recall facts database --category decision

# Generate project-specific context
.venv/bin/python src/inject.py --project myproject

# Print memory context to stdout (used by hooks)
.venv/bin/python src/inject.py --stdout

# Deduplicate similar facts by embedding similarity
.venv/bin/python src/distill.py dedup
# Custom similarity threshold (default 0.9)
.venv/bin/python src/distill.py dedup --threshold 0.85

# Detect cross-project patterns
.venv/bin/python src/distill.py patterns
# Promote detected patterns to global facts
.venv/bin/python src/distill.py patterns --promote

# Backfill fact embeddings for existing facts
.venv/bin/python src/distill.py backfill_embeddings

# Clear and re-embed all messages with a new model
.venv/bin/python src/embed.py build --reembed

# Use an alternative embedding model for dedup
.venv/bin/python src/distill.py run --embed-model mpnet

# Disable conversation segmentation
.venv/bin/python src/distill.py run --no-segment
```

### Fact Curation

The system auto-extracts facts, but hand-curated facts are the highest value:

```bash
# Interactive review of auto-extracted facts
python3 src/curate.py review

# Import facts from a markdown file
cp curated-facts.example.md curated-facts.md
# Edit curated-facts.md with your facts, then:
python3 src/curate.py import curated-facts.md

# Export high-confidence facts
python3 src/curate.py export

# View stats
python3 src/curate.py stats
```

## Web UI

A browser-based search engine and curation interface for your memory.

### Starting the Web UI

```bash
cd /path/to/rollyourownmemory
.venv/bin/python -m uvicorn src.web:app --host 0.0.0.0 --port 8585
```

Then open http://localhost:8585 in your browser.

### Features

- **Search** — Full-text search across messages, facts, and sessions. Toggle semantic search for meaning-based results. When semantic search is enabled, semantic fact matches are included alongside message results.
- **Ask** — Get LLM-synthesized answers from your memory with source citations (requires ollama). Uses both FTS and semantic search for facts and messages to gather comprehensive context.
- **Fact Management** — Browse, filter, edit, and delete facts. Adjust confidence scores. Filter by category, project, source tool, or confidence range.
- **CLAUDE.md Editor** — Edit your `~/.claude/CLAUDE.md` with live markdown preview.
- **Context Preview** — See what `inject.py` would generate for `memory-context.md`. Adjust token budget and project filter.

## Project-Aware Context

`inject.py` auto-detects the current project from `$PWD` and filters the context accordingly. When you run Claude Code from `~/myproject/`, the memory context emphasizes facts and sessions related to that project.

To add your own projects, edit the `PROJECT_ALIASES` dict in `src/inject.py`:

```python
PROJECT_ALIASES = {
    "myproject": "myproject",
    "my-other-project": "other",
}
```

## Multi-Tool Support

The ingest pipeline automatically discovers and parses conversation logs from three AI coding tools:

| Tool | Log Location | Format |
|------|-------------|--------|
| Claude Code | `~/.claude/projects/*/`, `~/.claude/history.jsonl` | JSONL with `type` field (user/assistant) |
| Factory.ai (Droid) | `~/.factory/sessions/*/` | JSONL with `role` field + tool calls |
| Codex CLI | `~/.codex/sessions/YYYY/MM/DD/*.jsonl`, `~/.codex/history.jsonl` | JSONL with `type` field + content blocks |

All conversations flow into the same database, searchable together. The `source_tool` column lets you filter by tool when needed (e.g., in the web UI's fact curation page, or via SQL queries).

No configuration needed — `ingest.py` auto-discovers all available log directories.

## Multi-Machine Support

If you work across multiple machines:

1. **Sync conversation logs** to one machine (rsync, Resilio Sync, Syncthing, etc.)
2. Run the ingest/embed/distill pipeline on that machine
3. The MCP server runs on the machine with the database

The `ingest.py` script supports custom source directories. See `discover_sources()` for the paths it checks.

## Team & Shared Memory

rollyourownmemory supports shared team memory through its existing multi-machine architecture. Here's how to set it up.

### Architecture Options

**Option 1: Shared Database (Simplest)**
Mount a shared filesystem (NFS, SSHFS, or a synced folder) and point all team members' `DB_PATH` to the same `memory.db`. SQLite WAL mode handles concurrent reads well, though concurrent writes should be serialized (only one person running `distill.py` at a time).

```bash
# In each team member's environment, set the DB path:
export MEMORY_DB_PATH=/shared/team/memory.db
```

Note: This requires adding `MEMORY_DB_PATH` environment variable support to each script (replace the hardcoded `DB_PATH` with `os.environ.get('MEMORY_DB_PATH', str(MEMORY_DIR / 'memory.db'))`).

**Option 2: Sync via Wormhole (Current Architecture)**
The project already supports ingesting logs from remote machines via `~/wormhole/claude-logs/`. Each team member syncs their Claude Code logs to a shared location, and one central instance runs `ingest.py` → `distill.py` → `embed.py` to build the combined database.

**Option 3: Per-User Partitioning**
For teams that want shared memory but per-user attribution, add a `user` column to the messages and facts tables:

```sql
ALTER TABLE messages ADD COLUMN user TEXT DEFAULT 'default';
ALTER TABLE facts ADD COLUMN user TEXT DEFAULT 'default';
```

Then filter by user in `inject.py` to show only relevant facts, or show all facts with user attribution.

### Privacy Considerations

- **All conversations are stored in plaintext** in SQLite. Anyone with DB access sees everything.
- The `machine` column in messages identifies which machine contributed each message.
- Facts extracted by `distill.py` may contain sensitive information from conversations.
- Consider running `curate.py` regularly to review and remove sensitive facts.
- For regulated environments, consider encrypting `memory.db` at rest.

### Team Setup Checklist

1. Choose an architecture option above
2. Set up log sync (rsync, wormhole, or shared mount)
3. Configure one machine to run the cron pipeline (ingest → embed → distill)
4. Each team member configures their Claude Code hooks (see Hook-Based Automation)
5. Use `memory_search_facts` to verify cross-machine facts are appearing

## Schema

```sql
messages        — id, source_file, session_id, project, role, content, timestamp, machine, source_tool
embeddings      — message_id, embedding (float32 blob), model
facts           — id, session_id, project, fact, category, confidence, source_message_id, compressed_details, source_tool
fact_embeddings — fact_id, embedding (float32 blob), model
entities        — id, name, entity_type, first_seen, last_seen, mention_count
entity_mentions — id, entity_id, message_id, session_id, timestamp
```

The `source_tool` column tracks which AI coding tool generated each message and fact (`claude_code`, `factory`, or `codex`).

FTS5 indexes on `messages.content` and `facts.fact` for fast keyword search. Porter stemming enabled.

## How the Pieces Fit Together

1. **You use your AI coding tools normally.** Conversations are saved as JSONL by Claude Code, Factory.ai, and/or Codex CLI.
2. **`ingest.py`** (cron, every 15 min) reads new JSONL data from all tools into SQLite, tagging each with its `source_tool`.
3. **`embed.py`** (cron, hourly) generates vector embeddings for new messages.
4. **`distill.py`** (cron, hourly) extracts facts from new sessions using regex + optionally a local LLM.
5. **`entities.py`** (cron, hourly) identifies tools/libraries/services mentioned.
6. **`inject.py`** (cron, every 30 min) generates `memory-context.md` with top facts, recent sessions, and active tech stack.
7. **`CLAUDE.md`** includes `@memory-context.md`, so every new session starts with context.
8. **`mcp_server.py`** lets Claude query the database directly mid-session for deeper recall.

## Configuration

### Embedding Model

Default: `all-MiniLM-L6-v2` (fast, 384 dimensions, ~90MB).

The model registry (`EMBEDDING_MODELS` in `embed.py`) supports short names for convenience:

| Short Name | Full Model Name | Dimensions |
|------------|----------------|------------|
| `minilm` | `all-MiniLM-L6-v2` | 384 |
| `mpnet` | `all-mpnet-base-v2` | 768 |

You can use either the short name or the full model name:

```bash
# Using short name
.venv/bin/python src/embed.py build --model mpnet

# Using full name
.venv/bin/python src/embed.py build --model all-mpnet-base-v2
```

**Switching models:** Use the `--reembed` flag to clear existing embeddings and re-embed all messages with a new model:

```bash
.venv/bin/python src/embed.py build --model mpnet --reembed
```

**Backfilling fact embeddings:** After upgrading, backfill embeddings for existing facts:

```bash
.venv/bin/python src/distill.py backfill_embeddings
```

**Dimension-mismatch safety:** If the database contains embeddings from a different model (different vector dimensions), brute-force and FAISS search will detect the mismatch and handle it gracefully instead of crashing. A warning is logged when a model change is detected.

### Conversation Segmentation

Long sessions are automatically split into topically coherent segments before fact extraction. The segmentation uses cosine drift detection between consecutive user messages to identify topic shifts.

This is **enabled by default** during `distill.py run`. To disable it:

```bash
.venv/bin/python src/distill.py run --no-segment
```

Segmentation improves fact extraction quality for long, multi-topic sessions by giving the LLM focused chunks of conversation rather than a single large transcript.

### LLM for Distillation

Default: `llama3.3:70b`. To use a different model:

```bash
.venv/bin/python src/distill.py run --llm --model llama3.3:70b
```

Any ollama model works — just pass its name via `--model`.

### Context Budget

`inject.py` defaults to ~2000 tokens (~8KB) of context. Adjust with `--max-tokens`:

```bash
.venv/bin/python src/inject.py --max-tokens 3000 -o ~/.claude/memory-context.md
```

Sections are assigned priorities (header=1, facts=2, focus=3, sessions=4, stack=5). When the budget is tight, lower-priority sections are dropped entirely rather than truncating mid-section — so you always get coherent context blocks.

## Maintenance

**Monthly (~5 min):**
- Run `python3 src/curate.py review` to approve/reject auto-extracted facts
- Check `ingest.log` for errors, truncate if large

**Quarterly (~15 min):**
- Review and update your `CLAUDE.md` files as projects evolve
- Update `PROJECT_ALIASES` in `src/inject.py` for new projects
- Update `KNOWN_ENTITIES` in `src/entities.py` for new tools you've adopted

**The single highest-value maintenance task** is occasionally hand-curating 10-20 facts in `curated-facts.md` and running `python3 src/curate.py import curated-facts.md`. These confidence-1.0 facts always surface first in context injection.

## Limitations

- **Semantic search loads embeddings into memory.** FAISS mitigates this, but `embed.py build` needs ~300 MB RAM. Use `--batch-size 64` on machines with less than 4 GB.
- **Fact extraction quality depends on the LLM.** The heuristic extractor catches obvious patterns; use `--llm` with a 7B+ parameter model for best results.
- **Multi-user support is experimental.** See the [Team & Shared Memory](#team--shared-memory) section for shared database options. All sessions, facts, and entities share one database with no built-in access control.
- **Schema migrations are additive only.** There is no automated rollback. Back up `memory.db` before upgrading.

## Privacy

- **Everything is local.** No data is sent to any external service.
- Embeddings are generated locally via sentence-transformers.
- Fact extraction uses a local LLM (ollama) or regex heuristics.
- The database, embeddings, and state files are in `.gitignore`.
- The MCP server communicates via stdio (no network port).

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — free to use, share, and adapt for non-commercial purposes with attribution.
