#!/usr/bin/env python3
"""Claude Memory MCP Server — exposes conversation memory as tools for Claude Code."""

import json
import sqlite3
from pathlib import Path
from typing import Optional

from mcp.server import FastMCP

MEMORY_DIR = Path(__file__).parent
DB_PATH = MEMORY_DIR / "memory.db"

mcp = FastMCP("claude-memory")


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def memory_search(query: str, limit: int = 5, project: Optional[str] = None,
                  role: Optional[str] = None, since: Optional[str] = None) -> str:
    """Search conversation memory using full-text keyword search.

    Args:
        query: Search terms (supports FTS5 syntax: AND, OR, quotes for phrases)
        limit: Max results to return (default 5)
        project: Filter by project name substring
        role: Filter by 'user' or 'assistant'
        since: Only results after this date (ISO 8601, e.g. '2026-03-01')
    """
    conn = get_conn()
    sql = """
        SELECT m.id, m.session_id, m.project, m.role, m.content,
               m.timestamp, m.machine
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH ?
    """
    params = [query]
    if project:
        sql += " AND m.project LIKE ?"
        params.append(f"%{project}%")
    if since:
        sql += " AND m.timestamp >= ?"
        params.append(since)
    if role:
        sql += " AND m.role = ?"
        params.append(role)
    sql += " ORDER BY messages_fts.rank LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return f"FTS5 query error: {e}. Use simple keywords or quoted phrases."

    conn.close()
    if not rows:
        return f"No results found for \"{query}\""

    results = []
    for r in rows:
        ts = (r["timestamp"] or "unknown")[:16]
        proj = r["project"] or "no-project"
        content = r["content"][:400] + "..." if len(r["content"]) > 400 else r["content"]
        results.append(f"[{ts}] {proj} [{r['machine'] or ''}] ({r['role']})\n{content}")

    return f"Found {len(rows)} results for \"{query}\":\n\n" + "\n\n---\n\n".join(results)


@mcp.tool()
def memory_semantic_search(query: str, limit: int = 5, project: Optional[str] = None,
                           role: Optional[str] = None) -> str:
    """Search conversation memory by meaning using vector embeddings.
    Use this when keyword search misses — it finds semantically similar content
    even if exact words don't match.

    Args:
        query: Natural language description of what you're looking for
        limit: Max results (default 5)
        project: Filter by project name substring
        role: Filter by 'user' or 'assistant'
    """
    try:
        from embed import search_similar
    except ImportError:
        return "Semantic search unavailable: sentence-transformers not installed."

    results = search_similar(query, top_k=limit, project=project, role=role,
                             decay_halflife_days=30)
    if not results:
        return f"No semantic matches for \"{query}\""

    lines = []
    for r in results:
        ts = (r.get("timestamp") or "unknown")[:16]
        proj = r.get("project") or "no-project"
        content = r.get("content", "")
        content = content[:400] + "..." if len(content) > 400 else content
        score = r.get("score", 0)
        lines.append(f"[{ts}] {proj} ({r.get('role', '?')}) score={score:.3f}\n{content}")

    return f"Found {len(results)} semantic matches for \"{query}\":\n\n" + "\n\n---\n\n".join(lines)


@mcp.tool()
def memory_get_session(session_id: str, limit: int = 50) -> str:
    """Retrieve the full conversation thread from a specific session.

    Args:
        session_id: Session ID (full UUID or prefix to match)
        limit: Max messages to return (default 50)
    """
    conn = get_conn()
    if len(session_id) < 36:
        sql = "SELECT * FROM messages WHERE session_id LIKE ? ORDER BY timestamp, id LIMIT ?"
        params = [f"{session_id}%", limit]
    else:
        sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp, id LIMIT ?"
        params = [session_id, limit]

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return f"No messages found for session \"{session_id}\""

    project = rows[0]["project"] or "no-project"
    lines = [f"Session {rows[0]['session_id'][:8]}... ({project}, {len(rows)} messages):\n"]
    for r in rows:
        ts = (r["timestamp"] or "")[:16].replace("T", " ")
        prefix = "USER" if r["role"] == "user" else "ASST"
        content = r["content"][:500] + "..." if len(r["content"]) > 500 else r["content"]
        lines.append(f"[{ts}] {prefix}: {content}")

    return "\n\n".join(lines)


@mcp.tool()
def memory_list_sessions(limit: int = 15, project: Optional[str] = None,
                         since: Optional[str] = None) -> str:
    """List recent conversation sessions with summaries.

    Args:
        limit: Max sessions to show (default 15)
        project: Filter by project name substring
        since: Only sessions after this date
    """
    conn = get_conn()
    sql = """
        SELECT session_id, project, machine,
               MIN(timestamp) as first_msg, MAX(timestamp) as last_msg,
               COUNT(*) as msg_count
        FROM messages WHERE session_id IS NOT NULL
    """
    params = []
    if project:
        sql += " AND project LIKE ?"
        params.append(f"%{project}%")
    if since:
        sql += " AND timestamp >= ?"
        params.append(since)
    sql += " GROUP BY session_id ORDER BY last_msg DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        conn.close()
        return "No sessions found."

    lines = []
    for r in rows:
        sid = (r["session_id"] or "unknown")[:8]
        proj = r["project"] or "no-project"
        date = (r["last_msg"] or "")[:10]
        topic_row = conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY timestamp LIMIT 1",
            (r["session_id"],),
        ).fetchone()
        topic = (topic_row[0][:80] + "...") if topic_row and len(topic_row[0]) > 80 else (topic_row[0] if topic_row else "")
        lines.append(f"{sid} [{date}] {proj} ({r['msg_count']} msgs): {topic}")

    conn.close()
    return f"Recent sessions:\n\n" + "\n".join(lines)


@mcp.tool()
def memory_search_facts(query: str, category: Optional[str] = None,
                        limit: int = 10) -> str:
    """Search extracted facts and knowledge from past conversations.

    Args:
        query: Search terms
        category: Filter by category: preference, decision, learning, context, tool, pattern
        limit: Max results (default 10)
    """
    conn = get_conn()
    sql = """
        SELECT f.* FROM facts_fts
        JOIN facts f ON f.id = facts_fts.rowid
        WHERE facts_fts MATCH ? AND f.confidence > 0
    """
    params = [query]
    if category:
        sql += " AND f.category = ?"
        params.append(category)
    sql += " ORDER BY f.confidence DESC, f.timestamp DESC LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return f"No facts found for \"{query}\""

    conn.close()
    if not rows:
        return f"No facts found for \"{query}\""

    lines = []
    for r in rows:
        ts = (r["timestamp"] or "")[:10]
        proj = r["project"] or "general"
        lines.append(f"[{r['category']}] (conf={r['confidence']:.1f}) {r['fact']}")

    return f"Facts matching \"{query}\":\n\n" + "\n".join(lines)


@mcp.tool()
def memory_add_fact(fact: str, category: str, project: Optional[str] = None) -> str:
    """Store a new fact or preference for long-term memory.
    Use this when the user shares something worth remembering across sessions.

    Args:
        fact: The fact to remember (concise statement)
        category: One of: preference, decision, learning, context, tool, pattern
        project: Optional project this fact relates to
    """
    valid = {"preference", "decision", "learning", "context", "tool", "pattern"}
    if category not in valid:
        return f"Invalid category. Must be one of: {', '.join(sorted(valid))}"

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO facts (fact, category, confidence, project) VALUES (?, ?, 1.0, ?)",
        (fact, category, project),
    )
    conn.commit()
    conn.close()
    return f"Stored fact: [{category}] {fact}"


@mcp.tool()
def memory_find_entity(name: str) -> str:
    """Find what sessions and contexts mention a specific tool, library, service, or concept.

    Args:
        name: Entity name to search for (e.g. 'kalshi', 'playwright', 'fastapi')
    """
    conn = get_conn()
    entity = conn.execute(
        "SELECT * FROM entities WHERE name = ? AND id > 0", (name.lower(),)
    ).fetchone()

    if not entity:
        conn.close()
        return f"No entity '{name}' found in memory."

    mentions = conn.execute("""
        SELECT DISTINCT em.session_id, m.project, m.timestamp, m.content
        FROM entity_mentions em
        JOIN messages m ON m.id = em.message_id
        WHERE em.entity_id = ?
        ORDER BY m.timestamp DESC LIMIT 10
    """, (entity["id"],)).fetchall()

    conn.close()

    lines = [f"Entity: {entity['name']} ({entity['entity_type']})",
             f"Mentions: {entity['mention_count']}x",
             f"First seen: {(entity['first_seen'] or '')[:10]}",
             f"Last seen: {(entity['last_seen'] or '')[:10]}", ""]

    if mentions:
        lines.append("Recent mentions:")
        for m in mentions:
            ts = (m["timestamp"] or "")[:10]
            proj = m["project"] or "no-project"
            snippet = m["content"][:120] + "..." if len(m["content"]) > 120 else m["content"]
            lines.append(f"  [{ts}] {proj}: {snippet}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
