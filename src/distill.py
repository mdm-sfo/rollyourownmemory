#!/usr/bin/env python3
"""Distillation pipeline — extract structured facts from conversation sessions.

Analyzes conversations to extract preferences, decisions, learnings, and patterns
into the facts table. Uses heuristic extraction by default, with optional LLM
enhancement when available.
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from src.memory_db import get_conn, get_session_messages
except ImportError:
    from memory_db import get_conn, get_session_messages

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"

FACT_CATEGORIES = {
    "preference": "User preferences, likes, dislikes, style choices",
    "decision": "Architectural or design decisions made during a session",
    "learning": "Something the user learned or discovered",
    "context": "Background context about the user's environment or goals",
    "tool": "Tools, libraries, or services the user works with",
    "pattern": "Recurring patterns or workflows",
    "error": "Error conditions encountered and their symptoms",
    "solution": "Fixes and workarounds for specific errors",
}

# Heuristic patterns for fact extraction
PREFERENCE_PATTERNS = [
    r"(?:I |i )(?:prefer|like|want|always use|usually|tend to)\s+(.{10,120})",
    r"(?:let's |let's )(?:use|go with|stick with)\s+(.{5,80})",
    r"(?:I |i )(?:don't like|hate|avoid|never use)\s+(.{10,120})",
]

DECISION_PATTERNS = [
    r"(?:let's |let's |we should |I'll |i'll )(?:go with|use|implement|switch to|migrate to)\s+(.{10,120})",
    r"(?:decided to|going to|plan is to)\s+(.{10,120})",
]

LEARNING_PATTERNS = [
    r"(?:TIL|I learned|I found out|turns out|apparently|discovered that)\s+(.{10,200})",
    r"(?:the (?:trick|key|solution|fix|issue) (?:is|was))\s+(.{10,200})",
]

CONTEXT_PATTERNS = [
    r"(?:I'm working on|my project|I'm building|I'm using|my setup|I run)\s+(.{10,150})",
    r"(?:the codebase|our stack|we use|our team)\s+(.{10,150})",
]


def extract_facts_heuristic(session_messages):
    """Extract facts from a session's messages using pattern matching."""
    facts = []
    user_messages = [m for m in session_messages if m["role"] == "user"]

    for msg in user_messages:
        content = msg["content"]
        if len(content) < 15 or len(content) > 5000:
            continue

        for pattern in PREFERENCE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                facts.append({
                    "fact": f"User preference: {match.group(0).strip()[:300]}",
                    "category": "preference",
                    "confidence": 0.6,
                    "source_message_id": msg["id"],
                    "session_id": msg.get("session_id"),
                    "project": msg.get("project"),
                    "timestamp": msg.get("timestamp"),
                })

        for pattern in DECISION_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                facts.append({
                    "fact": f"Decision: {match.group(0).strip()[:300]}",
                    "category": "decision",
                    "confidence": 0.6,
                    "source_message_id": msg["id"],
                    "session_id": msg.get("session_id"),
                    "project": msg.get("project"),
                    "timestamp": msg.get("timestamp"),
                })

        for pattern in LEARNING_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                facts.append({
                    "fact": f"Learning: {match.group(0).strip()[:300]}",
                    "category": "learning",
                    "confidence": 0.5,
                    "source_message_id": msg["id"],
                    "session_id": msg.get("session_id"),
                    "project": msg.get("project"),
                    "timestamp": msg.get("timestamp"),
                })

        for pattern in CONTEXT_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                facts.append({
                    "fact": f"Context: {match.group(0).strip()[:300]}",
                    "category": "context",
                    "confidence": 0.5,
                    "source_message_id": msg["id"],
                    "session_id": msg.get("session_id"),
                    "project": msg.get("project"),
                    "timestamp": msg.get("timestamp"),
                })

    return facts


def extract_facts_llm(session_messages, api_base=None, model: str = "llama3.3:70b",
                      existing_facts=None):
    """Extract facts using a local LLM via OpenAI-compatible API.

    Works with ollama, vllm, llama.cpp server, or any OpenAI-compatible endpoint.

    Args:
        session_messages: List of message dicts from a session.
        api_base: OpenAI-compatible API base URL.
        model: LLM model name (default: llama3.3:70b).
        existing_facts: Optional list of fact strings already extracted — the LLM
            will be instructed not to re-extract these.
    """
    try:
        import httpx
    except ImportError:
        print("httpx required for LLM extraction: pip install httpx", file=sys.stderr)
        return []

    base = api_base or "http://localhost:11434/v1"
    user_msgs = [m for m in session_messages if m["role"] == "user"]
    if not user_msgs:
        return []

    conversation = "\n".join(
        f"[{m['role']}] {m['content'][:2000]}" for m in session_messages[:100]
    )

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
- "error" = specific error conditions worth remembering (include error text, affected component, and trigger conditions)
- "solution" = the fix or workaround for a specific error (reference what error it solves)

Do NOT extract:
- Transient requests ("fix this bug", "show me the output")
- Conversational filler
- Facts about the AI assistant itself
- Anything only relevant to this one session

When you find an error/solution pair, extract BOTH as separate facts:
- The error fact should include enough detail to recognize the problem if it recurs
- The solution fact should reference the error and include the specific fix
Example:
  {{"fact": "CORS preflight fails when Authorization header present on cross-origin /api/v2 requests", "category": "error", "compressed_details": "full error message text, browser console output"}}
  {{"fact": "Fix CORS preflight: add explicit OPTIONS handler with Access-Control-Allow-Headers: Authorization in Express router", "category": "solution", "compressed_details": "exact middleware code, header list"}}
{existing_context}
Return a JSON array of objects with these keys:
- "fact": concise statement of the durable fact
- "category": one of the categories above
- "compressed_details": comma-separated list of specifics you omitted from the fact (e.g. "exact config values, error message text, specific file paths"). If nothing was omitted, use "none".

Be highly selective — 3-8 facts max per conversation. If nothing new is worth extracting, return an empty array [].

Conversation:
{conversation[:24000]}

Return ONLY a JSON array, no other text."""

    def _parse_llm_facts(text: str) -> list[dict]:
        """Parse facts from LLM response text. Returns list of fact dicts or raises ValueError."""
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            raise ValueError("No JSON array found in response")
        raw_facts = json.loads(match.group())
        facts = []
        for f in raw_facts:
            if "fact" in f and "category" in f and f["category"] in FACT_CATEGORIES:
                facts.append({
                    "fact": f["fact"][:500],
                    "category": f["category"],
                    "compressed_details": (f.get("compressed_details") or "")[:500],
                    "confidence": 0.9,
                    "source_message_id": user_msgs[0]["id"],
                    "session_id": user_msgs[0].get("session_id"),
                    "project": user_msgs[0].get("project"),
                    "timestamp": user_msgs[0].get("timestamp"),
                })
        return facts

    # Three-level retry: normal → conservative → heuristic fallback
    # Level 1: Normal attempt (temp 0.1, timeout 180s)
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_llm_facts(text)
    except Exception as e:
        reason = str(e)
        print(
            f"LLM extraction: normal attempt failed ({reason}), "
            "retrying with conservative settings...",
            file=sys.stderr,
        )

    # Level 2: Retry with lower temperature and shorter timeout
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.05,
            },
            timeout=90,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_llm_facts(text)
    except Exception as e:
        reason = str(e)
        print(
            f"LLM extraction: retry also failed ({reason}), "
            "falling back to heuristic only",
            file=sys.stderr,
        )

    # Level 3: Return empty — caller runs heuristic extraction separately
    return []


def get_undistilled_sessions(conn):
    """Find sessions that haven't been distilled yet."""
    return conn.execute("""
        SELECT DISTINCT m.session_id
        FROM messages m
        WHERE m.session_id IS NOT NULL
        AND m.session_id NOT IN (
            SELECT DISTINCT session_id FROM facts WHERE session_id IS NOT NULL
        )
        ORDER BY m.timestamp
    """).fetchall()


_embedding_model = None


def _get_dedup_model():
    """Lazy-load the sentence-transformer model for deduplication."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from src.embed import get_model as _get_embed_model, DEFAULT_MODEL as _embed_default
        except ImportError:
            from embed import get_model as _get_embed_model, DEFAULT_MODEL as _embed_default
        _embedding_model = _get_embed_model(_embed_default)
    return _embedding_model


def _compute_embedding(text: str):
    """Compute a normalized embedding for a fact text string."""
    import numpy as np
    model = _get_dedup_model()
    vec = model.encode([text[:2048]], normalize_embeddings=True)[0]
    return vec.astype(np.float32)


def _load_existing_fact_embeddings(conn: sqlite3.Connection):
    """Load existing facts with confidence > 0 and compute their embeddings on the fly.

    Returns (facts_list, embeddings_matrix) where embeddings_matrix is (N, dim).
    Returns empty lists/array if no qualifying facts exist.
    """
    import numpy as np
    rows = conn.execute(
        "SELECT id, fact, confidence FROM facts WHERE confidence > 0"
    ).fetchall()
    if not rows:
        return [], np.array([])

    facts_list = [dict(r) for r in rows]
    model = _get_dedup_model()
    texts = [f["fact"][:2048] for f in facts_list]
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return facts_list, embeddings.astype(np.float32)


def _is_near_duplicate(candidate_vec, existing_embeddings,
                       threshold: float = 0.85) -> Optional[int]:
    """Check if candidate_vec is a near-duplicate of any existing embedding.

    Returns the index of the most similar existing fact if similarity > threshold,
    or None if no near-duplicate found.
    """
    import numpy as np
    if existing_embeddings.size == 0:
        return None
    similarities = existing_embeddings @ candidate_vec
    max_idx = int(np.argmax(similarities))
    if similarities[max_idx] > threshold:
        return max_idx
    return None


def store_facts(conn, facts):
    """Store facts, skipping near-duplicates based on embedding cosine similarity."""
    import numpy as np

    # Load existing fact embeddings for dedup check
    existing_facts, existing_embeddings = _load_existing_fact_embeddings(conn)

    inserted = 0
    skipped = 0
    for f in facts:
        # Check for near-duplicate before inserting
        candidate_vec = _compute_embedding(f["fact"])
        dup_idx = _is_near_duplicate(candidate_vec, existing_embeddings)
        if dup_idx is not None:
            existing = existing_facts[dup_idx]
            print(
                f"Skipping near-duplicate fact (similarity >0.85): "
                f"'{f['fact'][:80]}...' ~ existing fact id={existing['id']}: "
                f"'{existing['fact'][:80]}...'",
                file=sys.stderr,
            )
            skipped += 1
            continue

        try:
            conn.execute(
                """INSERT INTO facts (session_id, project, fact, category, confidence,
                   source_message_id, timestamp, compressed_details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (f["session_id"], f["project"], f["fact"], f["category"],
                 f["confidence"], f["source_message_id"], f["timestamp"],
                 (f.get("compressed_details") or "")),
            )
            inserted += 1
            # Add the newly inserted fact to existing embeddings for subsequent checks
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            existing_facts.append({"id": new_id, "fact": f["fact"], "confidence": f["confidence"]})
            if existing_embeddings.size == 0:
                existing_embeddings = candidate_vec.reshape(1, -1)
            else:
                existing_embeddings = np.vstack([existing_embeddings, candidate_vec.reshape(1, -1)])
        except sqlite3.IntegrityError:
            pass

    if skipped:
        print(f"Dedup: {skipped} near-duplicate fact(s) skipped.", file=sys.stderr)
    return inserted


def dedup_facts(db_path: Optional[str] = None, threshold: float = 0.85) -> int:
    """Scan existing facts for near-duplicate clusters and remove lower-confidence duplicates.

    Keeps the highest-confidence fact per cluster, deletes the rest.
    Returns the number of facts removed.
    """
    import numpy as np

    conn = get_conn(db_path or str(DB_PATH))
    rows = conn.execute(
        "SELECT id, fact, confidence FROM facts WHERE confidence > 0 ORDER BY id"
    ).fetchall()

    if not rows:
        print("No facts with confidence > 0 found.")
        conn.close()
        return 0

    facts_list = [dict(r) for r in rows]
    model = _get_dedup_model()
    texts = [f["fact"][:2048] for f in facts_list]
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True).astype(np.float32)

    n = len(facts_list)
    print(f"Scanning {n} facts for near-duplicates (threshold={threshold})...")

    # Union-Find for clustering
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Compute pairwise similarities and build clusters
    similarity_matrix = embeddings @ embeddings.T
    for i in range(n):
        for j in range(i + 1, n):
            if similarity_matrix[i, j] > threshold:
                union(i, j)

    # Group by cluster
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(i)

    # Find clusters with duplicates and decide what to remove
    to_delete: list[int] = []
    report_lines: list[str] = []

    for root, members in sorted(clusters.items()):
        if len(members) < 2:
            continue

        # Sort by confidence descending, then by id ascending (keep earliest among ties)
        member_facts = [(idx, facts_list[idx]) for idx in members]
        member_facts.sort(key=lambda x: (-x[1]["confidence"], x[1]["id"]))

        keeper_idx, keeper = member_facts[0]
        report_lines.append(f"\nCluster (keeping id={keeper['id']}, conf={keeper['confidence']:.2f}):")
        report_lines.append(f"  KEEP: [{keeper['id']}] {keeper['fact'][:100]}")

        for idx, fact in member_facts[1:]:
            to_delete.append(fact["id"])
            report_lines.append(f"  DEL:  [{fact['id']}] (conf={fact['confidence']:.2f}) {fact['fact'][:100]}")

    if not to_delete:
        print("No duplicate clusters found.")
        conn.close()
        return 0

    # Print report
    print(f"\n=== Deduplication Report ===")
    print(f"Found {len(report_lines)} lines in {sum(1 for m in clusters.values() if len(m) >= 2)} cluster(s)")
    for line in report_lines:
        print(line)

    # Delete duplicates (FTS index cleaned up by facts_ad trigger)
    placeholders = ",".join("?" * len(to_delete))
    conn.execute(f"DELETE FROM facts WHERE id IN ({placeholders})", to_delete)
    conn.commit()
    conn.close()

    print(f"\nRemoved {len(to_delete)} duplicate fact(s).")
    return len(to_delete)


def detect_cross_project_patterns(conn: sqlite3.Connection, model: Optional[str] = None,
                                   min_projects: int = 3) -> list[dict]:
    """Find facts that repeat across multiple projects and promote to global patterns.

    Groups facts by project, sends the summary to a local LLM for cross-project
    analysis, and returns a list of pattern dicts.

    Args:
        conn: SQLite database connection.
        model: LLM model name (default: llama3.3:70b).
        min_projects: Minimum number of projects required for pattern detection.
    """
    try:
        import httpx
    except ImportError:
        print("httpx required for pattern detection: pip install httpx", file=sys.stderr)
        return []

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
    projects: dict[str, list[dict]] = {}
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
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            patterns = json.loads(match.group())
            return patterns
    except Exception as e:
        print(f"Pattern detection failed: {e}", file=sys.stderr)

    return []


def distill(use_llm=False, api_base=None, limit=None, model: str = "llama3.3:70b"):
    conn = get_conn(str(DB_PATH))
    sessions = get_undistilled_sessions(conn)

    if limit:
        sessions = sessions[:limit]

    if not sessions:
        print("All sessions already distilled.")
        conn.close()
        return

    # Load existing facts for dedup context
    existing_facts_rows = conn.execute(
        "SELECT fact FROM facts WHERE confidence > 0 ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    existing_facts = [r[0] for r in existing_facts_rows]

    print(f"Distilling {len(sessions)} sessions...")
    total_facts = 0

    for (session_id,) in sessions:
        messages = get_session_messages(conn, session_id, limit=10000)
        if len(messages) < 2:
            # Store a sentinel so we don't re-check empty sessions
            conn.execute(
                "INSERT OR IGNORE INTO facts (session_id, fact, category, confidence) VALUES (?, ?, ?, ?)",
                (session_id, f"[session with {len(messages)} message(s)]", "context", 0.0),
            )
            conn.commit()
            continue

        facts = extract_facts_heuristic(messages)

        if use_llm:
            llm_facts = extract_facts_llm(messages, api_base, model=model,
                                          existing_facts=existing_facts)
            facts.extend(llm_facts)

        if facts:
            inserted = store_facts(conn, facts)
            total_facts += inserted
        else:
            # Sentinel for sessions with no extractable facts
            conn.execute(
                "INSERT OR IGNORE INTO facts (session_id, fact, category, confidence) VALUES (?, ?, ?, ?)",
                (session_id, "[no facts extracted]", "context", 0.0),
            )

        conn.commit()

    conn.close()
    print(f"Done. {total_facts} facts extracted from {len(sessions)} sessions.")


def main():
    parser = argparse.ArgumentParser(description="Claude Memory Distillation")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Distill facts from undistilled sessions")
    run.add_argument("--llm", action="store_true", help="Use local LLM for enhanced extraction")
    run.add_argument("--model", default="llama3.3:70b", help="LLM model name (default: llama3.3:70b)")
    run.add_argument("--api-base", help="OpenAI-compatible API base URL (default: localhost:11434)")
    run.add_argument("--limit", type=int, help="Max sessions to process")

    show = sub.add_parser("show", help="Show extracted facts")
    show.add_argument("--category", "-c", choices=list(FACT_CATEGORIES.keys()))
    show.add_argument("--project", "-p")
    show.add_argument("--search", "-s", help="FTS search within facts")
    show.add_argument("--limit", "-n", type=int, default=20)
    show.add_argument("--min-confidence", type=float, default=0.0)

    dedup = sub.add_parser("dedup", help="Remove near-duplicate facts via embedding similarity")
    dedup.add_argument("--threshold", type=float, default=0.85,
                       help="Cosine similarity threshold (default: 0.85)")

    patterns_cmd = sub.add_parser("patterns", help="Detect cross-project patterns")
    patterns_cmd.add_argument("--model", help="LLM model name (default: llama3.3:70b)")
    patterns_cmd.add_argument("--min-projects", type=int, default=3,
                              help="Minimum projects for pattern detection (default: 3)")
    patterns_cmd.add_argument("--promote", action="store_true",
                              help="Auto-promote detected patterns to global facts")

    stats = sub.add_parser("stats", help="Show distillation statistics")

    args = parser.parse_args()

    if args.command == "run":
        distill(use_llm=args.llm, api_base=args.api_base, limit=args.limit, model=args.model)
    elif args.command == "show":
        conn = get_conn(str(DB_PATH))

        if args.search:
            sql = """
                SELECT f.* FROM facts_fts
                JOIN facts f ON f.id = facts_fts.rowid
                WHERE facts_fts MATCH ?
                AND f.confidence >= ?
            """
            params = [args.search, args.min_confidence]
        else:
            sql = "SELECT * FROM facts WHERE confidence >= ?"
            params = [args.min_confidence]

        if args.category:
            sql += " AND category = ?"
            params.append(args.category)
        if args.project:
            sql += " AND project LIKE ?"
            params.append(f"%{args.project}%")

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(args.limit)

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        if not rows:
            print("No facts found.")
            sys.exit(1)

        for r in rows:
            ts = (r["timestamp"] or "unknown")
            if "T" in str(ts):
                ts = str(ts).split("T")[0]
            proj = r["project"] or "general"
            print(f"[{ts}] [{r['category']}] (conf={r['confidence']:.1f}) {proj}")
            print(f"  {r['fact']}\n")

    elif args.command == "dedup":
        dedup_facts(threshold=args.threshold)
    elif args.command == "patterns":
        conn = get_conn(str(DB_PATH))
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
                            (f"[cross-project] {p.get('pattern', '')}", p.get('category', 'pattern'))
                        )
                        promoted += 1
                    except (sqlite3.IntegrityError, KeyError):
                        pass
                conn.commit()
                print(f"\nPromoted {promoted} patterns to global facts.")
        else:
            print("No cross-project patterns detected.")
        conn.close()
    elif args.command == "stats":
        conn = get_conn(str(DB_PATH))
        total_sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM messages").fetchone()[0]
        distilled = conn.execute("SELECT COUNT(DISTINCT session_id) FROM facts").fetchone()[0]
        total_facts = conn.execute("SELECT COUNT(*) FROM facts WHERE confidence > 0").fetchone()[0]
        print(f"Sessions: {total_sessions} ({distilled} distilled)")
        print(f"Facts:    {total_facts}")
        cats = conn.execute(
            "SELECT category, COUNT(*) FROM facts WHERE confidence > 0 GROUP BY category"
        ).fetchall()
        for cat, count in cats:
            print(f"  {cat}: {count}")
        conn.close()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
