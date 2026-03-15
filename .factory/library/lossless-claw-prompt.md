# Complete Improvements for rollyourownmemory — Lossless-Claw Insights + Missing Use Cases

## Context

You are modifying the `rollyourownmemory` project — a local memory system for Claude Code that uses SQLite + FTS5 + sentence-transformer embeddings + local LLM fact extraction. The codebase lives in a repo with this structure:

```
schema.sql
src/distill.py
src/inject.py
src/mcp_server.py
src/ingest.py
src/embed.py
src/entities.py
src/curate.py
bin/claude-recall
CLAUDE.md
README.md
```

This prompt covers TWO sets of improvements:

1. **Tasks 1-6**: Inspired by the Martian Engineering "lossless-claw" project (a DAG-based context compaction system). We are NOT porting its architecture — we are selectively adopting its best ideas into our flat fact-store architecture.

2. **Tasks 7-12**: Missing use cases that make the system dramatically more valuable — hook-based automation, error/solution persistence, session continuity, cross-project patterns, confidence feedback, and team memory documentation.

**CRITICAL**: The default LLM model is `llama3.3:70b`. Do NOT change this default anywhere. The user runs a DGX Spark with 128GB unified memory. A `--model` CLI flag for override is fine, but the hardcoded default must remain `llama3.3:70b`.

Execute these tasks in order. Each task builds on the previous one. Commit after each task with a descriptive message.

---

## Analysis: What's Applicable and What Users Will Notice

This section explains the reasoning behind each improvement — what it changes in the codebase, and what the end user actually experiences differently. The implementation tasks follow in the next section.

### Confirmed Applicable — With Concrete UX Impact

#### 1. "Details compressed" footer on extracted facts

**Where it lands**: `distill.py`, specifically the LLM prompt in `extract_facts_llm()`

**What the user notices**: When Claude Code calls `memory_search_facts` and gets back a fact like "Decision: using JWT with refresh tokens", it currently has no signal about whether that's the whole story or a compressed summary. With a "compressed from:" tag, the agent sees: "Decision: using JWT with refresh tokens [compressed: cookie storage config, refresh rotation logic, logout invalidation]" — and knows to call `memory_search` for the full session if those details matter. The user stops getting confidently wrong answers from half-remembered context.

**Verdict**: Directly applicable. Small change to the LLM prompt, stored as an extra column or appended to the fact text. → **Task 1 + Task 2a**

#### 2. Previous-context dedup in distillation

**Where it lands**: `distill.py` → `extract_facts_llm()`. Right now it processes each session in isolation — the prompt gets the conversation but zero awareness of already-extracted facts.

**What the user notices**: Today, if you discuss "I use Tailscale for networking" in 5 sessions, you get 5 near-duplicate facts clogging `memory_search_facts` results and `inject.py` output. With prior-fact context, the LLM only extracts genuinely new information. The user sees a cleaner, less repetitive `memory-context.md` — and `inject.py`'s 2000-token budget goes further because it's not wasting slots on duplicates.

**Verdict**: Directly applicable. Pass the last 10-15 extracted facts for that project into the prompt with "Do not re-extract these — only extract what is new or changed." → **Task 2a + Task 2c**

#### 3. Three-level failure handling in distill.py

**Where it lands**: `extract_facts_llm()` has a single `try/except` that silently returns `[]` on any failure.

**What the user notices**: If `llama3.3:70b` is under load, times out, or returns malformed JSON (which happens with local models), the session is marked as "distilled" via the sentinel row but zero facts are captured. The user never knows a session was lost. With retry + fallback, more sessions produce facts. The user notices their fact count actually matches reality — and can see in logs when the fallback kicked in. Practically: fewer "Wait, I know we discussed this — why doesn't memory have it?" moments.

**Verdict**: Directly applicable. Retry once with lower temperature, then fall back to heuristic extraction rather than returning empty. → **Task 2b**

#### 4. Token-budget-aware inject.py

**Where it lands**: `inject.py` → `generate_memory_context()`. It currently does a crude `max_chars = max_tokens * 4` truncation at the end, after assembling everything.

**What the user notices**: As the DB grows (you're at 670+ facts and 13k messages), the raw assembly before truncation gets bigger, but the truncation is dumb — it just cuts at a character count, potentially mid-section. With priority-based selection (high-confidence first, most recent next, then entity-matched), the user gets the most relevant 2000 tokens instead of the first 2000 characters. The difference becomes noticeable once you cross ~100 facts — you start getting stale facts instead of important recent ones.

**Verdict**: Directly applicable. `inject.py` already has the `max_tokens` parameter and the truncation logic — it just needs smarter selection before truncation rather than after. → **Task 3**

#### 5. MCP tool escalation (inspect + deep_recall)

**Where it lands**: `mcp_server.py`. Currently offers `memory_search`, `memory_semantic_search`, `memory_search_facts`, `memory_get_session`, `memory_list_sessions`, `memory_find_entity`, `memory_add_fact`.

**What the user notices**: Today, when Claude Code finds a relevant fact via `memory_search_facts`, it has no way to drill deeper without the user manually asking it to `memory_get_session` with the right session ID. A `memory_inspect` tool (given a fact ID, return the source message + related entities + sibling facts from that session) creates a one-step drill-down. The user's experience: they say "how did we solve that CORS issue?", Claude finds the fact, drills into the source session automatically, and gives a complete answer — instead of saying "I found a fact about CORS, want me to look up the session?"

A `memory_deep_recall` tool (given a query, retrieve source messages across sessions, synthesize via local LLM) is more ambitious and depends on the 70B being fast enough for interactive use. For the DGX Spark, this is fine.

**Verdict**: `memory_inspect` is a clear win and straightforward. `memory_deep_recall` is applicable but a larger lift — worth it once the basics are solid. → **Task 4 + Task 5**

### Applicable But Less User-Visible

#### 6. Depth-aware re-distillation in curate.py

**Where it lands**: `curate.py` (future work, not implemented in this prompt)

**What the user notices**: Not much immediately. This is a long-term DB health feature. After 6+ months, when you have 2000+ facts, a periodic pass that merges 20 granular facts about "kalshi" into 3-4 high-level ones keeps `inject.py` output sharp. The user would notice this as "memory-context.md still feels relevant and tight even though I've been using this for a year" vs. "it's bloated with old noise now."

**Verdict**: Real but not urgent. Park this until your fact count hits ~1500+. Not included in the tasks below.

### Missing Use Cases — What Transforms This From a Tool Into a Platform

#### 7. Hook-based automatic ingest (no more cron lag)

**Where it lands**: New file `hooks/memory-hook.sh` + `~/.claude/settings.json` configuration + minor changes to `ingest.py` and `inject.py`

**What the user notices**: Today the entire pipeline runs on cron — meaning conversations aren't ingested until the next cron tick (typically 5-30 minutes). If you discuss something in session A and immediately start session B, session B has no memory of session A. With Claude Code hooks:

- **SessionStart** fires at the beginning of every session and injects `memory-context.md` via `additionalContext` — Claude starts every session already knowing your preferences, recent work, and key decisions. No more "we discussed this yesterday" frustration.
- **SessionEnd** fires when a session closes, triggering `ingest.py` immediately — the conversation is in SQLite within seconds, not minutes.
- **PreCompact** fires before context compaction (when the context window fills up), triggering `ingest.py` + `distill.py` — facts are extracted from the conversation that's about to be compressed, so nothing is lost even if the compacted summary drops details.

The user experience changes from "memory catches up eventually" to "memory is always current." This is the single highest-impact improvement.

**Verdict**: Directly applicable, highest priority. → **Task 7**

#### 8. Error/solution persistence as structured fact categories

**Where it lands**: `src/distill.py` (FACT_CATEGORIES dict + LLM prompt)

**What the user notices**: When the user spends 45 minutes debugging a CORS issue, the current system might extract a generic "context" fact like "had CORS issues with the API." With dedicated "error" and "solution" categories, distill.py extracts structured pairs: the error fact ("CORS preflight fails when Authorization header is present on cross-origin requests to /api/v2") and the matching solution fact ("Fixed by adding explicit OPTIONS handler in Express with Access-Control-Allow-Headers: Authorization"). The next time the user hits the same error — or a teammate does — `memory_search_facts` returns the exact fix. The user stops re-debugging solved problems.

**Verdict**: Directly applicable. Small change to the category dict and the LLM prompt. → **Task 8**

#### 9. `memory_resume_context` MCP tool — "pick up where I left off"

**Where it lands**: `src/mcp_server.py`

**What the user notices**: The user closes Claude Code, comes back the next day, and says "let's continue working on the auth system." Today, Claude has to search facts, guess what was in progress, and ask clarifying questions. With `memory_resume_context`, Claude calls one tool that returns: the last session's key messages, what files were being edited, which decisions were made but not yet implemented, and any open threads. The user's experience: they say "pick up where we left off" and Claude actually does. No re-explaining, no context reconstruction — it just works.

**Verdict**: Directly applicable. Leverages existing session/message/fact data. → **Task 9**

#### 10. Cross-project pattern detection

**Where it lands**: `src/distill.py` (new function) or a new `src/patterns.py` module

**What the user notices**: The user makes the same architectural decision across 4 projects (e.g., always choosing SQLite for local storage, always structuring repos the same way). Today, these are stored as separate per-project facts. With cross-project detection, the system notices "this user always chooses X" and promotes it to a global preference. The user notices that Claude starts proactively suggesting their preferred patterns in new projects without being told. The effect is subtle but powerful: Claude feels like it actually knows them, not just their current project.

**Verdict**: Applicable but a larger lift. Worth doing after the foundation is solid. → **Task 10**

#### 11. Confidence calibration feedback loop

**Where it lands**: `src/mcp_server.py` (new tool) + `schema.sql` (minor)

**What the user notices**: Today, all facts start with confidence 0.9 and never change. If distill.py extracts a wrong fact ("user prefers React" when they actually said they prefer Vue), the only fix is `curate.py` manual removal. With a feedback tool, when Claude surfaces a fact and the user says "that's not right" or "exactly right," Claude can call `memory_feedback` to adjust confidence up/down. Over time, accurate facts rise to the top of inject.py output and wrong ones sink below the threshold. The user notices: memory gets more accurate the more they use it, instead of accumulating noise.

**Verdict**: Directly applicable. Simple tool + confidence update. → **Task 11**

#### 12. Team/shared memory documentation

**Where it lands**: `README.md` (new section)

**What the user notices**: The user already syncs conversation logs across machines via wormhole. The multi-machine architecture is there but undocumented for team use. With a clear "Team Memory" section in README, users understand how to: share a single memory.db across a team, partition facts by user via a simple schema addition, set up cross-machine sync for collaborative memory, and understand the privacy implications. The user notices: they can onboard a teammate in 10 minutes instead of reverse-engineering the sync setup.

**Verdict**: Documentation only, no code changes. → **Task 12**

---

## Implementation Tasks

### Part 1: Lossless-Claw Inspired Improvements (Tasks 1-6)

## Task 1: Add `compressed_details` column to facts table

**File**: `schema.sql`

Add a `compressed_details` TEXT column to the `facts` table. This stores a comma-separated list of specifics that were omitted when the fact was extracted, so consuming agents know what details exist but were compressed away.

```sql
-- Add after the existing columns in the facts table:
compressed_details TEXT,           -- comma-separated list of specifics omitted during extraction
```

Also add `compressed_details` to the `facts_fts` virtual table so it's searchable:

Change the facts_fts definition from:
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact,
    category,
    content='facts',
    content_rowid='id',
    tokenize='porter unicode61'
);
```

To:
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact,
    category,
    compressed_details,
    content='facts',
    content_rowid='id',
    tokenize='porter unicode61'
);
```

Update ALL three FTS triggers (`facts_ai`, `facts_au`, `facts_ad`) to include the `compressed_details` column in their INSERT statements. For example, `facts_ai` becomes:

```sql
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact, category, compressed_details)
    VALUES (new.id, new.fact, new.category, new.compressed_details);
END;
```

Apply the same pattern to `facts_au` (both the DELETE and INSERT lines) and `facts_ad`.

Write a migration note as a SQL comment at the top of schema.sql:
```sql
-- Migration: If upgrading an existing database, run:
--   ALTER TABLE facts ADD COLUMN compressed_details TEXT;
--   DROP TABLE IF EXISTS facts_fts;
--   -- Then re-run the CREATE VIRTUAL TABLE and trigger statements below
--   -- Then rebuild FTS: INSERT INTO facts_fts(facts_fts) VALUES('rebuild');
```

---

## Task 2: Update distill.py LLM prompt to extract compressed details + deduplicate against prior facts

**File**: `src/distill.py`

### 2a: Update the LLM prompt

In `extract_facts_llm()`, replace the current prompt with one that:

1. Asks the LLM to include a `"compressed_details"` key in each fact object — a comma-separated string listing specifics that were omitted from the fact summary.
2. Accepts an optional `existing_facts` parameter (list of strings) representing facts already extracted for this project. The prompt instructs the LLM to NOT re-extract facts that already exist.

Change the function signature to:
```python
def extract_facts_llm(session_messages, api_base=None, model=None, existing_facts=None):
```

The `model` parameter defaults to `"llama3.3:70b"` if not provided. Use it in the httpx POST body instead of the hardcoded string. Example:
```python
model_name = model or "llama3.3:70b"
```

Replace the prompt. The new prompt should be:

```python
    existing_context = ""
    if existing_facts:
        facts_list = "\n".join(f"- {f}" for f in existing_facts[:30])
        existing_context = f"""
ALREADY KNOWN FACTS (do NOT re-extract these or minor variations of them):
{facts_list}

Only extract facts that are GENUINELY NEW information not covered above.
"""

    prompt = f"""You are analyzing a conversation between a user and an AI coding assistant. Extract ONLY durable, reusable facts — things that would be valuable to know in future sessions.

Categories: {list(FACT_CATEGORIES.keys())}

Rules:
- "preference" = lasting opinions or style choices (NOT one-off requests like "show me X")
- "decision" = architectural or design choices that affect future work
- "learning" = discoveries, gotchas, or insights worth remembering
- "context" = persistent background info about the user's environment, projects, or goals
- "tool" = tools, libraries, or services the user relies on and how they use them
- "pattern" = recurring workflows or approaches

Do NOT extract:
- Transient requests ("fix this bug", "show me the output")
- Conversational filler
- Facts about the AI assistant itself
- Anything only relevant to this one session
{existing_context}
Return a JSON array of objects with these keys:
- "fact": concise statement of the durable fact
- "category": one of the categories above
- "compressed_details": comma-separated list of specifics you omitted from the fact (e.g. "exact config values, error message text, specific file paths"). If nothing was omitted, use "none".

Be highly selective — 3-8 facts max per conversation. If nothing new is worth extracting, return an empty array [].

Conversation:
{conversation[:24000]}

Return ONLY a JSON array, no other text."""
```

Update the fact parsing to extract `compressed_details`:
```python
            for f in raw_facts:
                if "fact" in f and "category" in f and f["category"] in FACT_CATEGORIES:
                    facts.append({
                        "fact": f["fact"][:500],
                        "category": f["category"],
                        "compressed_details": f.get("compressed_details", "")[:500],
                        "confidence": 0.9,
                        "source_message_id": user_msgs[0]["id"],
                        "session_id": user_msgs[0].get("session_id"),
                        "project": user_msgs[0].get("project"),
                        "timestamp": user_msgs[0].get("timestamp"),
                    })
```

### 2b: Add three-level failure handling

Wrap the LLM call in `extract_facts_llm()` with retry logic. The current code has a single try/except that returns `[]` on any failure. Replace it with:

1. **Normal attempt**: Current call with `temperature: 0.1`
2. **Retry**: If the first attempt returns no parseable JSON or throws an exception, retry once with `temperature: 0.05` and a shorter timeout (90s instead of 180s)
3. **Fallback to heuristic**: If retry also fails, log a warning and return `[]` (the caller already runs heuristic extraction separately, so this is fine)

Log each escalation level to stderr so the user can see it in cron logs:
```python
print(f"LLM extraction: normal attempt failed ({reason}), retrying with conservative settings...", file=sys.stderr)
```
```python
print(f"LLM extraction: retry also failed ({reason}), falling back to heuristic only", file=sys.stderr)
```

### 2c: Feed existing facts into the LLM call

In the `distill()` function, before the per-session loop, load existing facts for dedup context:

```python
    # Load existing facts for dedup context
    existing_facts_rows = conn.execute(
        "SELECT fact FROM facts WHERE confidence > 0 ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    existing_facts = [r[0] for r in existing_facts_rows]
```

Then pass them to `extract_facts_llm()`:
```python
        if use_llm:
            llm_facts = extract_facts_llm(messages, api_base, model=model, existing_facts=existing_facts)
            facts.extend(llm_facts)
```

### 2d: Update `store_facts()` to include compressed_details

Update the INSERT statement in `store_facts()`:
```python
def store_facts(conn, facts):
    inserted = 0
    for f in facts:
        try:
            conn.execute(
                """INSERT INTO facts (session_id, project, fact, category, confidence,
                   source_message_id, timestamp, compressed_details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (f["session_id"], f["project"], f["fact"], f["category"],
                 f["confidence"], f["source_message_id"], f["timestamp"],
                 f.get("compressed_details", "")),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    return inserted
```

### 2e: Add --model CLI flag

Add a `--model` argument to the `run` subparser:
```python
    run.add_argument("--model", help="LLM model name (default: llama3.3:70b)")
```

Pass it through to `distill()` and then to `extract_facts_llm()`. The `distill()` function signature becomes:
```python
def distill(use_llm=False, api_base=None, model=None, limit=None):
```

And the call becomes:
```python
            llm_facts = extract_facts_llm(messages, api_base, model=model, existing_facts=existing_facts)
```

And in `main()`:
```python
        distill(use_llm=args.llm, api_base=args.api_base, model=args.model, limit=args.limit)
```

---

## Task 3: Smart token-budget selection in inject.py

**File**: `src/inject.py`

The current `generate_memory_context()` assembles all sections and then does a dumb character truncation at the end:
```python
    max_chars = max_tokens * 4
    if len(output) > max_chars:
        output = output[:max_chars] + "\n\n[...truncated to fit token budget]"
```

Replace this with priority-based section assembly. The idea: build each section independently, estimate its token cost, then assemble sections in priority order until the budget is full.

Refactor `generate_memory_context()` to:

1. Build each section as a separate string (you already do this with the `sections` list — just keep it as a list of `(priority, section_text)` tuples).
2. Priority order (1 = highest):
   - Priority 1: Header (always included, ~10 tokens)
   - Priority 2: Key Facts section (most valuable)  
   - Priority 3: Focus-specific recall (if `--focus` was provided)
   - Priority 4: Recent Sessions
   - Priority 5: Active Stack (entities)
3. After building all sections, assemble them in priority order. For each section, estimate tokens as `len(section_text) // 4`. If adding the next section would exceed `max_tokens`, skip it.
4. If even the Key Facts section alone exceeds the budget, truncate it by reducing the LIMIT on the SQL query (try 10, then 5) until it fits.

Replace the final truncation block with:
```python
    # Priority-based assembly
    budget_chars = max_tokens * 4
    assembled = header  # always include
    used = len(header)
    
    for _priority, section_text in sorted(prioritized_sections):
        if used + len(section_text) + 4 <= budget_chars:  # +4 for \n\n separator
            assembled += "\n\n" + section_text
            used += len(section_text) + 4
        # else: skip this section — doesn't fit
    
    return assembled if len(assembled) > len(header) + 5 else "# Memory Context\n\nNo memory data available yet."
```

The key change: instead of building one big string and chopping it, we build sections independently and assemble what fits. This means the user always gets their highest-value facts even if the budget is tight, instead of getting a random mid-sentence cutoff.

---

## Task 4: Add `memory_inspect` MCP tool

**File**: `src/mcp_server.py`

Add a new tool that lets Claude Code drill into a specific fact to see its full context. This is the "describe" equivalent from lossless-claw's escalation pattern.

```python
@mcp.tool()
def memory_inspect(fact_id: int) -> str:
    """Inspect a specific fact with full context: source message, sibling facts from the same session, and related entities.
    
    Use this after memory_search_facts returns a relevant fact but you need more detail.
    The compressed_details field tells you what specifics were omitted during extraction.

    Args:
        fact_id: The fact ID from memory_search_facts results
    """
    conn = get_conn()
    
    # Get the fact
    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if not fact:
        conn.close()
        return f"No fact found with id {fact_id}"
    
    lines = [
        f"## Fact #{fact['id']}",
        f"**Category**: {fact['category']}",
        f"**Confidence**: {fact['confidence']:.1f}",
        f"**Project**: {fact['project'] or 'general'}",
        f"**Extracted**: {(fact['timestamp'] or 'unknown')[:16]}",
        f"**Fact**: {fact['fact']}",
    ]
    
    # Show compressed details if present
    compressed = fact["compressed_details"] if "compressed_details" in fact.keys() else None
    if compressed and compressed.strip() and compressed.strip() != "none":
        lines.append(f"**Details compressed**: {compressed}")
        lines.append("(Use memory_search or memory_get_session to recover these details)")
    
    # Get source message if available
    if fact["source_message_id"]:
        msg = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (fact["source_message_id"],)
        ).fetchone()
        if msg:
            lines.append("")
            lines.append("## Source Message")
            ts = (msg["timestamp"] or "")[:16].replace("T", " ")
            content = msg["content"][:800] + "..." if len(msg["content"]) > 800 else msg["content"]
            lines.append(f"[{ts}] ({msg['role']}) {content}")
            
            # Get session_id for sibling lookup
            session_id = msg["session_id"]
        else:
            session_id = fact["session_id"]
    else:
        session_id = fact["session_id"]
    
    # Get sibling facts from the same session
    if session_id:
        siblings = conn.execute(
            "SELECT id, fact, category, confidence FROM facts WHERE session_id = ? AND id != ? AND confidence > 0 ORDER BY id",
            (session_id, fact_id),
        ).fetchall()
        if siblings:
            lines.append("")
            lines.append("## Other Facts from Same Session")
            for s in siblings[:10]:
                lines.append(f"- [#{s['id']}] [{s['category']}] {s['fact']}")
    
    # Get related entities mentioned in the same session
    if session_id:
        entities = conn.execute("""
            SELECT DISTINCT e.name, e.entity_type, e.mention_count
            FROM entity_mentions em
            JOIN entities e ON e.id = em.entity_id
            WHERE em.session_id = ? AND e.id > 0
            ORDER BY e.mention_count DESC LIMIT 10
        """, (session_id,)).fetchall()
        if entities:
            lines.append("")
            lines.append("## Entities from Same Session")
            for e in entities:
                lines.append(f"- {e['name']} ({e['entity_type']}, {e['mention_count']}x total)")
    
    conn.close()
    return "\n".join(lines)
```

Also update `memory_search_facts` to include the fact ID and compressed_details in its output, so the user knows they can call `memory_inspect`:

In the `memory_search_facts` tool, change the output formatting from:
```python
    for r in rows:
        ts = (r["timestamp"] or "")[:10]
        proj = r["project"] or "general"
        lines.append(f"[{r['category']}] (conf={r['confidence']:.1f}) {r['fact']}")
```

To:
```python
    for r in rows:
        ts = (r["timestamp"] or "")[:10]
        proj = r["project"] or "general"
        compressed = ""
        try:
            cd = r["compressed_details"]
            if cd and cd.strip() and cd.strip() != "none":
                compressed = f" [details compressed: {cd}]"
        except (IndexError, KeyError):
            pass
        lines.append(f"[#{r['id']}] [{r['category']}] (conf={r['confidence']:.1f}) {r['fact']}{compressed}")
```

And update the return line to mention `memory_inspect`:
```python
    return f"Facts matching \"{query}\" (use memory_inspect(fact_id) for full context):\n\n" + "\n".join(lines)
```

---

## Task 5: Add `memory_deep_recall` MCP tool

**File**: `src/mcp_server.py`

This is the "expand_query" equivalent. Given a query, it retrieves relevant source messages across sessions and optionally synthesizes an answer via the local LLM.

```python
@mcp.tool()
def memory_deep_recall(query: str, synthesize: bool = True, limit: int = 10,
                       project: Optional[str] = None) -> str:
    """Deep recall: search across facts AND messages, gather full context, and optionally
    synthesize an answer using the local LLM. Use this when memory_search_facts finds
    relevant facts but you need the complete picture.

    Args:
        query: What you want to recall (natural language)
        synthesize: If True, use local LLM to synthesize a coherent answer from retrieved context (default True)
        limit: Max source messages to retrieve (default 10)
        project: Filter by project name substring
    """
    conn = get_conn()
    
    # Step 1: Find relevant facts
    fact_lines = []
    try:
        sql = """
            SELECT f.fact, f.category, f.session_id, f.compressed_details
            FROM facts_fts
            JOIN facts f ON f.id = facts_fts.rowid
            WHERE facts_fts MATCH ? AND f.confidence > 0
            ORDER BY f.confidence DESC LIMIT 5
        """
        fact_rows = conn.execute(sql, (query,)).fetchall()
        for r in fact_rows:
            compressed = ""
            try:
                cd = r["compressed_details"]
                if cd and cd.strip() and cd.strip() != "none":
                    compressed = f" (compressed: {cd})"
            except (IndexError, KeyError):
                pass
            fact_lines.append(f"- [{r['category']}] {r['fact']}{compressed}")
    except sqlite3.OperationalError:
        pass
    
    # Step 2: Find relevant messages via FTS
    msg_lines = []
    try:
        sql = """
            SELECT m.content, m.role, m.timestamp, m.project, m.session_id
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            WHERE messages_fts MATCH ?
        """
        params = [query]
        if project:
            sql += " AND m.project LIKE ?"
            params.append(f"%{project}%")
        sql += " ORDER BY messages_fts.rank LIMIT ?"
        params.append(limit)
        
        msg_rows = conn.execute(sql, params).fetchall()
        for r in msg_rows:
            ts = (r["timestamp"] or "")[:16]
            proj = r["project"] or "no-project"
            content = r["content"][:600] + "..." if len(r["content"]) > 600 else r["content"]
            msg_lines.append(f"[{ts}] {proj} ({r['role']}): {content}")
    except sqlite3.OperationalError:
        pass
    
    conn.close()
    
    if not fact_lines and not msg_lines:
        return f"No memories found for \"{query}\""
    
    # Step 3: Optionally synthesize with local LLM
    if synthesize and (fact_lines or msg_lines):
        try:
            import httpx
            
            context_parts = []
            if fact_lines:
                context_parts.append("EXTRACTED FACTS:\n" + "\n".join(fact_lines))
            if msg_lines:
                context_parts.append("SOURCE MESSAGES:\n" + "\n".join(msg_lines[:8]))
            
            context = "\n\n".join(context_parts)
            
            synth_prompt = f"""Based on the following memory context, provide a concise, accurate answer to this question: "{query}"

{context}

Rules:
- Only state things supported by the context above
- If the context is insufficient, say what you found and what's missing
- Be concise — this answer will be used as context in another conversation
- Include specific details (file paths, commands, config values) when available"""
            
            resp = httpx.post(
                "http://localhost:11434/v1/chat/completions",
                json={
                    "model": "llama3.3:70b",
                    "messages": [{"role": "user", "content": synth_prompt}],
                    "temperature": 0.1,
                },
                timeout=120,
            )
            resp.raise_for_status()
            synthesis = resp.json()["choices"][0]["message"]["content"]
            
            result_parts = [f"## Deep Recall: \"{query}\"\n", synthesis, ""]
            if fact_lines:
                result_parts.append("### Supporting Facts")
                result_parts.extend(fact_lines)
            result_parts.append(f"\n### Source Messages ({len(msg_lines)} found)")
            for ml in msg_lines[:5]:
                result_parts.append(ml)
            
            return "\n".join(result_parts)
            
        except Exception as e:
            # LLM unavailable — fall through to raw results
            pass
    
    # Fallback: return raw results without synthesis
    result_parts = [f"## Deep Recall: \"{query}\" (raw — LLM synthesis unavailable)\n"]
    if fact_lines:
        result_parts.append("### Extracted Facts")
        result_parts.extend(fact_lines)
    if msg_lines:
        result_parts.append("\n### Source Messages")
        result_parts.extend(msg_lines)
    
    return "\n".join(result_parts)
```

---

## Task 6: Update CLAUDE.md and README.md

### CLAUDE.md

Add the new MCP tools to the Key Files table and Usage section:

In the architecture section, update the MCP tools list to include `memory_inspect` and `memory_deep_recall`.

### README.md

In the architecture diagram, add `memory_inspect` and `memory_deep_recall` to the MCP Tools list.

In the "MCP Tools" usage section, add a new scenario demonstrating the escalation pattern:

```markdown
**Drill into compressed details:**
> *"How exactly did we configure the JWT refresh logic?"*
>
> Claude searches facts, finds "Decision: using JWT with refresh tokens" with compressed details listing "cookie config, rotation logic, logout invalidation". Claude calls `memory_inspect` to get the source message, then `memory_deep_recall` to synthesize the complete answer from all related context — delivering the exact configuration without you re-explaining anything.
```

In the "What's In the Box" table, update the `mcp_server.py` description to mention the new tools.

In the Schema section, add `compressed_details` to the facts table description.

---

### Part 2: Missing Use Cases (Tasks 7-12)

## Task 7: Hook-based automatic ingest via Claude Code hooks

**Files**: New file `hooks/memory-hook.sh`, documentation updates to `README.md`

This is the single highest-impact improvement. Instead of relying solely on cron, Claude Code hooks trigger the memory pipeline at the exact right moments: session start (inject context), session end (ingest new data), and pre-compaction (preserve context about to be compressed).

### 7a: Create the hook script

Create `hooks/memory-hook.sh`:

```bash
#!/usr/bin/env bash
# Claude Code memory hook — triggered by SessionStart, SessionEnd, and PreCompact.
# Reads hook event JSON from stdin and runs the appropriate memory pipeline step.

set -euo pipefail

# Resolve the rollyourownmemory project root (parent of hooks/)
MEMORY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Read stdin JSON into a variable
INPUT=$(cat)

# Extract event name and source/reason
EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null || echo "")
SOURCE=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('source', d.get('reason', d.get('trigger',''))))" 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")

log() {
    echo "[memory-hook] $(date +%H:%M:%S) $*" >&2
}

case "$EVENT" in
    SessionStart)
        log "Session starting (source=$SOURCE, session=$SESSION_ID)"
        # Run inject.py to generate memory-context.md, output it as additionalContext
        CONTEXT=$(cd "$MEMORY_ROOT" && python3 src/inject.py --max-tokens 2000 2>/dev/null || echo "")
        if [ -n "$CONTEXT" ]; then
            # Output JSON with additionalContext for Claude to receive
            python3 -c "
import json, sys
context = sys.stdin.read()
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': context
    }
}))
" <<< "$CONTEXT"
        fi
        ;;

    SessionEnd)
        log "Session ending (reason=$SOURCE, session=$SESSION_ID)"
        # Ingest the latest conversation data immediately
        cd "$MEMORY_ROOT" && python3 src/ingest.py --quiet 2>/dev/null &
        # Run embed in background too (non-blocking — SessionEnd has 1.5s timeout)
        cd "$MEMORY_ROOT" && python3 src/embed.py --quiet 2>/dev/null &
        ;;

    PreCompact)
        log "Pre-compaction (trigger=$SOURCE, session=$SESSION_ID)"
        # Ingest + distill BEFORE compaction strips detail from the context window
        cd "$MEMORY_ROOT" && python3 src/ingest.py --quiet 2>/dev/null || true
        cd "$MEMORY_ROOT" && python3 src/distill.py run --llm --limit 3 2>/dev/null &
        ;;

    *)
        log "Unknown event: $EVENT"
        ;;
esac

exit 0
```

Make it executable:
```bash
chmod +x hooks/memory-hook.sh
```

### 7b: Add settings.json configuration instructions to README.md

Add a new section to README.md under "Setup" (after the cron instructions) titled "Hook-Based Automation (Recommended)":

```markdown
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
- **SessionStart**: Runs `inject.py` and feeds memory context directly into Claude's context window via `additionalContext`. Claude starts every session already knowing your preferences and recent work.
- **SessionEnd**: Triggers `ingest.py` + `embed.py` in the background. Your conversation is in the database within seconds of closing, not at the next cron tick.
- **PreCompact**: Runs `ingest.py` + `distill.py` before the context window is compressed. Facts are extracted from the full conversation before compaction strips detail.

**Note**: Hooks complement cron — keep your cron job as a safety net for edge cases where hooks don't fire (e.g., force-quit). The hook handles the latency-sensitive path; cron handles the durability path.
```

### 7c: Update `inject.py` to support stdout output for hook use

Currently `inject.py` writes to a file (`memory-context.md`). The hook needs it to output to stdout so the hook script can capture it. Add a `--stdout` flag:

In `inject.py`'s `main()` function, add the argument:
```python
    parser.add_argument("--stdout", action="store_true",
                        help="Output to stdout instead of writing to file (for hook use)")
```

And update the output logic — if `--stdout` is set, print the context to stdout instead of writing to the file:
```python
    if args.stdout:
        print(output)
    else:
        output_path.write_text(output)
        print(f"Wrote {len(output)} chars to {output_path}")
```

Update the hook script's inject call to use `--stdout`:
```bash
CONTEXT=$(cd "$MEMORY_ROOT" && python3 src/inject.py --stdout --max-tokens 2000 2>/dev/null || echo "")
```

---

## Task 8: Add "error" and "solution" fact categories to distill.py

**File**: `src/distill.py`

Add two new categories to `FACT_CATEGORIES` for structured error/solution persistence:

```python
FACT_CATEGORIES = {
    "preference": "User preferences and style choices",
    "decision": "Architectural and design decisions",
    "learning": "Discoveries, gotchas, and insights",
    "context": "Background info about user's environment",
    "tool": "Tools, libraries, and services",
    "pattern": "Recurring workflows or approaches",
    "error": "Error conditions encountered and their symptoms",
    "solution": "Fixes and workarounds for specific errors",
}
```

Update the LLM prompt in `extract_facts_llm()` to include guidance for these categories. Add to the Rules section:

```python
- "error" = specific error conditions worth remembering (include error text, affected component, and trigger conditions)
- "solution" = the fix or workaround for a specific error (reference what error it solves)
```

Also add this guidance to the prompt:

```python
When you find an error/solution pair, extract BOTH as separate facts:
- The error fact should include enough detail to recognize the problem if it recurs
- The solution fact should reference the error and include the specific fix
Example:
  {"fact": "CORS preflight fails when Authorization header present on cross-origin /api/v2 requests", "category": "error", "compressed_details": "full error message text, browser console output"}
  {"fact": "Fix CORS preflight: add explicit OPTIONS handler with Access-Control-Allow-Headers: Authorization in Express router", "category": "solution", "compressed_details": "exact middleware code, header list"}
```

---

## Task 9: Add `memory_resume_context` MCP tool

**File**: `src/mcp_server.py`

This tool provides "pick up where I left off" session continuity. Given a project name (optional), it assembles: the last session's key messages, recent facts, and any entities active in that session.

```python
@mcp.tool()
def memory_resume_context(project: Optional[str] = None, session_id: Optional[str] = None) -> str:
    """Resume context from a previous session. Returns the last session's key messages,
    recent decisions, and what was being worked on. Use this when the user says
    "pick up where I left off" or "continue from last time."

    Args:
        project: Filter by project name substring (optional)
        session_id: Specific session ID to resume from (optional, defaults to most recent)
    """
    conn = get_conn()
    
    # Find the target session
    if session_id:
        target_session = session_id
    else:
        sql = "SELECT DISTINCT session_id, MAX(timestamp) as last_ts FROM messages WHERE session_id IS NOT NULL"
        params = []
        if project:
            sql += " AND project LIKE ?"
            params.append(f"%{project}%")
        sql += " GROUP BY session_id ORDER BY last_ts DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if not row:
            conn.close()
            return "No previous sessions found" + (f" for project matching '{project}'" if project else "")
        target_session = row["session_id"]
    
    lines = ["## Resume Context\n"]
    
    # Get session messages (last N user and assistant messages)
    messages = conn.execute(
        """SELECT role, content, timestamp, project FROM messages
           WHERE session_id = ? AND role IN ('user', 'assistant')
           ORDER BY timestamp DESC LIMIT 20""",
        (target_session,)
    ).fetchall()
    
    if messages:
        proj = messages[0]["project"] or "unknown"
        first_ts = (messages[-1]["timestamp"] or "")[:16]
        last_ts = (messages[0]["timestamp"] or "")[:16]
        lines.append(f"**Project**: {proj}")
        lines.append(f"**Session**: {target_session[:12]}...")
        lines.append(f"**Time range**: {first_ts} → {last_ts}")
        lines.append("")
        
        # Show last few user messages as "what was being discussed"
        user_msgs = [m for m in messages if m["role"] == "user"]
        lines.append("### Last User Messages")
        for m in reversed(user_msgs[:5]):
            ts = (m["timestamp"] or "")[:16].replace("T", " ")
            content = m["content"][:300] + "..." if len(m["content"]) > 300 else m["content"]
            lines.append(f"- [{ts}] {content}")
        lines.append("")
    
    # Get facts from this session
    facts = conn.execute(
        """SELECT fact, category, compressed_details FROM facts
           WHERE session_id = ? AND confidence > 0
           ORDER BY id""",
        (target_session,)
    ).fetchall()
    
    if facts:
        lines.append("### Decisions & Findings from This Session")
        for f in facts:
            compressed = ""
            try:
                cd = f["compressed_details"]
                if cd and cd.strip() and cd.strip() != "none":
                    compressed = f" (details: {cd})"
            except (IndexError, KeyError):
                pass
            lines.append(f"- [{f['category']}] {f['fact']}{compressed}")
        lines.append("")
    
    # Get entities from this session
    entities = conn.execute("""
        SELECT DISTINCT e.name, e.entity_type
        FROM entity_mentions em
        JOIN entities e ON e.id = em.entity_id
        WHERE em.session_id = ? AND e.id > 0
        ORDER BY e.mention_count DESC LIMIT 15
    """, (target_session,)).fetchall()
    
    if entities:
        lines.append("### Entities in Play")
        for e in entities:
            lines.append(f"- {e['name']} ({e['entity_type']})")
        lines.append("")
    
    # Get most recent facts across all sessions for broader context
    recent_facts = conn.execute(
        """SELECT fact, category FROM facts
           WHERE confidence > 0 AND session_id != ?
           ORDER BY timestamp DESC LIMIT 5""",
        (target_session,)
    ).fetchall()
    
    if recent_facts:
        lines.append("### Recent Facts from Other Sessions")
        for f in recent_facts:
            lines.append(f"- [{f['category']}] {f['fact']}")
    
    conn.close()
    return "\n".join(lines)
```

---

## Task 10: Cross-project pattern detection

**File**: `src/distill.py` (new function + CLI subcommand)

Add a `patterns` subcommand that scans facts across all projects to find recurring themes. This promotes per-project facts into global preferences when the same pattern appears in 3+ projects.

### 10a: Add the detection function

```python
def detect_cross_project_patterns(conn, model=None, min_projects=3):
    """Find facts that repeat across multiple projects and promote to global patterns."""
    import httpx
    
    # Get all facts grouped by project
    rows = conn.execute("""
        SELECT project, fact, category, confidence, id
        FROM facts
        WHERE confidence > 0 AND project IS NOT NULL
        ORDER BY project, category
    """).fetchall()
    
    if not rows:
        print("No project-specific facts to analyze.")
        return []
    
    # Group facts by project
    projects = {}
    for r in rows:
        proj = r["project"]
        if proj not in projects:
            projects[proj] = []
        projects[proj].append({"fact": r["fact"], "category": r["category"], "id": r["id"]})
    
    if len(projects) < min_projects:
        print(f"Only {len(projects)} projects found, need {min_projects}+ for pattern detection.")
        return []
    
    # Build a summary for the LLM
    summary_parts = []
    for proj, facts in projects.items():
        proj_name = proj.split("/")[-1] if proj else "unknown"
        fact_text = "\n".join(f"  - [{f['category']}] {f['fact']}" for f in facts[:20])
        summary_parts.append(f"Project: {proj_name}\n{fact_text}")
    
    summary = "\n\n".join(summary_parts)
    
    model_name = model or "llama3.3:70b"
    
    prompt = f"""Analyze these facts from {len(projects)} different projects. Find patterns that repeat across 3 or more projects — these represent the user's global preferences, habits, or architectural style.

Facts by project:
{summary[:16000]}

For each cross-project pattern you find, return a JSON object with:
- "pattern": a concise description of the cross-project pattern
- "category": the most appropriate fact category (preference, decision, pattern, tool)
- "evidence": list of project names where this pattern appears
- "source_fact_ids": list of fact IDs that support this pattern

Return a JSON array. If no clear cross-project patterns exist, return [].

Return ONLY a JSON array, no other text."""
    
    try:
        resp = httpx.post(
            "http://localhost:11434/v1/chat/completions",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=180,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        
        # Parse JSON from response
        import re
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            patterns = json.loads(match.group())
            return patterns
    except Exception as e:
        print(f"Pattern detection failed: {e}", file=sys.stderr)
    
    return []
```

### 10b: Add the CLI subcommand

In the `main()` function, add a `patterns` subparser:

```python
    patterns_cmd = sub.add_parser("patterns", help="Detect cross-project patterns")
    patterns_cmd.add_argument("--model", help="LLM model name (default: llama3.3:70b)")
    patterns_cmd.add_argument("--min-projects", type=int, default=3,
                              help="Minimum projects for pattern detection (default: 3)")
    patterns_cmd.add_argument("--promote", action="store_true",
                              help="Auto-promote detected patterns to global facts")
```

And the handler:

```python
    elif args.command == "patterns":
        conn = _connect()
        patterns = detect_cross_project_patterns(conn, model=args.model,
                                                 min_projects=args.min_projects)
        if patterns:
            print(f"\nDetected {len(patterns)} cross-project patterns:\n")
            for i, p in enumerate(patterns, 1):
                print(f"{i}. [{p.get('category', '?')}] {p.get('pattern', '?')}")
                evidence = p.get('evidence', [])
                print(f"   Found in: {', '.join(evidence)}")
            
            if args.promote:
                promoted = 0
                for p in patterns:
                    try:
                        conn.execute(
                            """INSERT INTO facts (project, fact, category, confidence, timestamp)
                               VALUES (NULL, ?, ?, 0.85, datetime('now'))""",
                            (f"[cross-project] {p['pattern']}", p.get('category', 'pattern'))
                        )
                        promoted += 1
                    except sqlite3.IntegrityError:
                        pass
                conn.commit()
                print(f"\nPromoted {promoted} patterns to global facts.")
        else:
            print("No cross-project patterns detected.")
        conn.close()
```

---

## Task 11: Confidence calibration feedback loop

**File**: `src/mcp_server.py`

Add a `memory_feedback` tool that lets Claude adjust fact confidence based on user reactions.

```python
@mcp.tool()
def memory_feedback(fact_id: int, feedback: str, correction: Optional[str] = None) -> str:
    """Provide feedback on a memory fact to calibrate confidence.
    Call this when the user confirms a fact is accurate ('correct', 'helpful')
    or indicates it's wrong ('wrong', 'outdated', 'irrelevant').

    Args:
        fact_id: The fact ID to provide feedback on
        feedback: One of: 'correct', 'helpful', 'wrong', 'outdated', 'irrelevant'
        correction: If feedback is 'wrong', the corrected version of the fact (optional)
    """
    conn = get_conn()
    
    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if not fact:
        conn.close()
        return f"No fact found with id {fact_id}"
    
    current_confidence = fact["confidence"]
    old_fact_text = fact["fact"]
    
    # Confidence adjustments
    adjustments = {
        "correct": 0.05,      # Small boost — confirmed accurate
        "helpful": 0.03,      # Smaller boost — useful but not explicitly confirmed
        "wrong": -0.5,        # Large penalty — incorrect information is dangerous
        "outdated": -0.3,     # Medium penalty — was correct, no longer is
        "irrelevant": -0.15,  # Small penalty — accurate but not useful
    }
    
    adjustment = adjustments.get(feedback, 0)
    if adjustment == 0:
        conn.close()
        return f"Unknown feedback type '{feedback}'. Use: correct, helpful, wrong, outdated, irrelevant"
    
    new_confidence = max(0.0, min(1.0, current_confidence + adjustment))
    
    # Update confidence
    conn.execute(
        "UPDATE facts SET confidence = ? WHERE id = ?",
        (new_confidence, fact_id)
    )
    
    result_parts = [f"Updated fact #{fact_id} confidence: {current_confidence:.2f} → {new_confidence:.2f}"]
    
    # If correction provided, insert the corrected fact and deprecate the old one
    if correction and feedback == "wrong":
        try:
            conn.execute(
                """INSERT INTO facts (session_id, project, fact, category, confidence,
                   source_message_id, timestamp, compressed_details)
                   VALUES (?, ?, ?, ?, 0.9, ?, datetime('now'), ?)""",
                (fact["session_id"], fact["project"], correction, fact["category"],
                 fact["source_message_id"],
                 f"corrected from: {old_fact_text[:200]}")
            )
            result_parts.append(f"Inserted corrected fact: {correction}")
        except sqlite3.IntegrityError:
            result_parts.append("Correction already exists in database")
    
    conn.commit()
    conn.close()
    
    # Add guidance for the agent
    if new_confidence <= 0.1:
        result_parts.append("Fact is now below visibility threshold — it won't appear in future context.")
    elif feedback in ("wrong", "outdated"):
        result_parts.append("Consider using memory_add_fact to record the correct/current information.")
    
    return "\n".join(result_parts)
```

Also update the `memory_search_facts` output to hint about feedback:

Change the return line (if not already updated by Task 4) to:
```python
    return f"Facts matching \"{query}\" (use memory_inspect for details, memory_feedback to correct):\n\n" + "\n".join(lines)
```

---

## Task 12: Team/shared memory documentation

**File**: `README.md`

Add a new section titled "Team & Shared Memory" after the existing "Multi-Machine Support" or setup section. This is documentation only — no code changes.

```markdown
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
```

---

## Testing

After all tasks are complete:

1. Verify `schema.sql` is valid SQL by running:
   ```bash
   sqlite3 :memory: < schema.sql
   ```

2. Verify all Python files parse correctly:
   ```bash
   for f in src/distill.py src/inject.py src/mcp_server.py src/ingest.py; do
     python3 -c "import ast; ast.parse(open('$f').read()); print('$f: OK')"
   done
   ```

3. Verify the hook script is executable and valid bash:
   ```bash
   bash -n hooks/memory-hook.sh && echo "hooks/memory-hook.sh: OK"
   ```

4. Run `distill.py stats` to make sure it still works against the existing schema (it should handle the missing column gracefully if run against an un-migrated DB).

5. Verify `distill.py patterns --help` works (Task 10):
   ```bash
   python3 src/distill.py patterns --help
   ```

6. Test `inject.py --stdout` outputs to terminal instead of writing to file (Task 7c):
   ```bash
   python3 src/inject.py --stdout --max-tokens 500 | head -5
   ```

7. Verify FACT_CATEGORIES now includes "error" and "solution" (Task 8):
   ```bash
   python3 -c "import importlib.util; spec = importlib.util.spec_from_file_location('d', 'src/distill.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); cats = mod.FACT_CATEGORIES; assert 'error' in cats and 'solution' in cats; print('Categories OK:', list(cats.keys()))"
   ```
