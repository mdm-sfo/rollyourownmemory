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

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"

FACT_CATEGORIES = {
    "preference": "User preferences, likes, dislikes, style choices",
    "decision": "Architectural or design decisions made during a session",
    "learning": "Something the user learned or discovered",
    "context": "Background context about the user's environment or goals",
    "tool": "Tools, libraries, or services the user works with",
    "pattern": "Recurring patterns or workflows",
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


def extract_facts_llm(session_messages, api_base=None):
    """Extract facts using a local LLM via OpenAI-compatible API.

    Works with ollama, vllm, llama.cpp server, or any OpenAI-compatible endpoint.
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

Return a JSON array of objects with "fact" and "category" keys. Be highly selective — 3-8 facts max per conversation. If nothing is worth extracting, return an empty array [].

Conversation:
{conversation[:24000]}

Return ONLY a JSON array, no other text."""

    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": "llama3.3:70b",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=180,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        # Extract JSON from response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            raw_facts = json.loads(match.group())
            facts = []
            for f in raw_facts:
                if "fact" in f and "category" in f and f["category"] in FACT_CATEGORIES:
                    facts.append({
                        "fact": f["fact"][:500],
                        "category": f["category"],
                        "confidence": 0.9,
                        "source_message_id": user_msgs[0]["id"],
                        "session_id": user_msgs[0].get("session_id"),
                        "project": user_msgs[0].get("project"),
                        "timestamp": user_msgs[0].get("timestamp"),
                    })
            return facts
    except Exception as e:
        print(f"LLM extraction failed: {e}", file=sys.stderr)
        return []

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


def get_session_messages(conn, session_id):
    rows = conn.execute(
        """SELECT id, session_id, project, role, content, timestamp, machine
           FROM messages WHERE session_id = ? ORDER BY timestamp, id""",
        (session_id,),
    ).fetchall()
    return [dict(zip(["id", "session_id", "project", "role", "content", "timestamp", "machine"], r))
            for r in rows]


def store_facts(conn, facts):
    inserted = 0
    for f in facts:
        try:
            conn.execute(
                """INSERT INTO facts (session_id, project, fact, category, confidence,
                   source_message_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f["session_id"], f["project"], f["fact"], f["category"],
                 f["confidence"], f["source_message_id"], f["timestamp"]),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    return inserted


def distill(use_llm=False, api_base=None, limit=None):
    conn = sqlite3.connect(str(DB_PATH))
    sessions = get_undistilled_sessions(conn)

    if limit:
        sessions = sessions[:limit]

    if not sessions:
        print("All sessions already distilled.")
        conn.close()
        return

    print(f"Distilling {len(sessions)} sessions...")
    total_facts = 0

    for (session_id,) in sessions:
        messages = get_session_messages(conn, session_id)
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
            llm_facts = extract_facts_llm(messages, api_base)
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
    run.add_argument("--api-base", help="OpenAI-compatible API base URL (default: localhost:11434)")
    run.add_argument("--limit", type=int, help="Max sessions to process")

    show = sub.add_parser("show", help="Show extracted facts")
    show.add_argument("--category", "-c", choices=list(FACT_CATEGORIES.keys()))
    show.add_argument("--project", "-p")
    show.add_argument("--search", "-s", help="FTS search within facts")
    show.add_argument("--limit", "-n", type=int, default=20)
    show.add_argument("--min-confidence", type=float, default=0.0)

    stats = sub.add_parser("stats", help="Show distillation statistics")

    args = parser.parse_args()

    if args.command == "run":
        distill(use_llm=args.llm, api_base=args.api_base, limit=args.limit)
    elif args.command == "show":
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

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

    elif args.command == "stats":
        conn = sqlite3.connect(str(DB_PATH))
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
