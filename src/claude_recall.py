#!/usr/bin/env python3
"""claude-recall — Search Claude Code conversation memory via FTS5, semantic search, and facts."""

import argparse
import sqlite3
import sys
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"

if not DB_PATH.exists():
    DB_PATH = Path.home() / "wormhole" / "claude-memory" / "memory.db"


def search_fts(query: str, project: str | None = None, since: str | None = None,
               limit: int = 5, role: str | None = None) -> list[dict]:
    """Full-text keyword search via FTS5."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT m.id, m.session_id, m.project, m.role, m.content,
               m.timestamp, m.machine, messages_fts.rank
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH ?
    """
    params: list = [query]

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
        if "fts5" in str(e).lower():
            print(f"FTS5 query error: {e}", file=sys.stderr)
            print("Tip: use simple keywords or quoted phrases", file=sys.stderr)
            sys.exit(1)
        raise
    finally:
        conn.close()

    return [dict(r) for r in rows]


def search_semantic(query: str, project: str | None = None, since: str | None = None,
                    limit: int = 5, role: str | None = None, decay: int = 30) -> list[dict]:
    """Vector similarity search via embeddings."""
    try:
        from src.embed import search_similar
    except ImportError:
        try:
            from embed import search_similar
        except ImportError:
            print("Semantic search requires: pip install sentence-transformers numpy", file=sys.stderr)
            sys.exit(1)

    return search_similar(
        query, top_k=limit, project=project,
        since=since, role=role, decay_halflife_days=decay,
    )


def search_facts(query: str, project: str | None = None, category: str | None = None,
                 limit: int = 10) -> list[dict]:
    """Search extracted facts."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT f.* FROM facts_fts
        JOIN facts f ON f.id = facts_fts.rowid
        WHERE facts_fts MATCH ?
        AND f.confidence > 0
    """
    params: list = [query]

    if project:
        sql += " AND f.project LIKE ?"
        params.append(f"%{project}%")
    if category:
        sql += " AND f.category = ?"
        params.append(category)

    sql += " ORDER BY f.confidence DESC, f.timestamp DESC LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    return [dict(r) for r in rows]


def get_session(session_id: str, limit: int = 50) -> list[dict]:
    """Retrieve all messages from a specific session."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Support partial session ID matching
    if len(session_id) < 36:
        sql = """
            SELECT id, session_id, project, role, content, timestamp, machine
            FROM messages
            WHERE session_id LIKE ?
            ORDER BY timestamp, id
            LIMIT ?
        """
        params = [f"{session_id}%", limit]
    else:
        sql = """
            SELECT id, session_id, project, role, content, timestamp, machine
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp, id
            LIMIT ?
        """
        params = [session_id, limit]

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_sessions(project: str | None = None, since: str | None = None,
                  limit: int = 20) -> list[dict]:
    """List recent sessions with summary info."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT session_id, project, machine,
               MIN(timestamp) as first_msg,
               MAX(timestamp) as last_msg,
               COUNT(*) as msg_count,
               GROUP_CONCAT(CASE WHEN role='user' THEN SUBSTR(content, 1, 80) END, ' | ') as snippets
        FROM messages
        WHERE session_id IS NOT NULL
    """
    params: list = []

    if project:
        sql += " AND project LIKE ?"
        params.append(f"%{project}%")
    if since:
        sql += " AND timestamp >= ?"
        params.append(since)

    sql += " GROUP BY session_id ORDER BY last_msg DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_message_results(rows: list[dict], query: str, mode: str = "fts") -> str | None:
    if not rows:
        return None

    label = "Semantic" if mode == "semantic" else "Keyword"
    lines = [f"## {label} Results for \"{query}\"\n"]

    for i, row in enumerate(rows, 1):
        ts = row.get("timestamp") or "unknown"
        if "T" in str(ts):
            ts = str(ts).split("T")[0]
        project = row.get("project") or "no-project"
        role = row.get("role", "?")
        machine = row.get("machine") or ""
        content = row.get("content", "")
        score = row.get("score")

        if len(content) > 400:
            content = content[:400] + "..."

        machine_tag = f" [{machine}]" if machine else ""
        score_tag = f" score={score:.3f}" if score is not None else ""
        lines.append(f"### {i}. [{ts}] {project}{machine_tag} ({role}){score_tag}")
        lines.append(f"> {content}\n")

    return "\n".join(lines)


def format_facts_results(rows: list[dict], query: str) -> str | None:
    if not rows:
        return None

    lines = [f"## Facts matching \"{query}\"\n"]
    for r in rows:
        ts = (r.get("timestamp") or "unknown")
        if "T" in str(ts):
            ts = str(ts).split("T")[0]
        proj = r.get("project") or "general"
        lines.append(f"- [{ts}] [{r['category']}] {proj}: {r['fact']}")

    return "\n".join(lines)


def format_session(rows: list[dict]) -> str | None:
    if not rows:
        return None

    session_id = rows[0].get("session_id", "unknown")
    project = rows[0].get("project") or "no-project"
    lines = [f"## Session {session_id[:8]}... ({project})\n"]

    for row in rows:
        ts = row.get("timestamp") or ""
        if "T" in str(ts):
            parts = str(ts).split("T")
            ts = f"{parts[0]} {parts[1][:8]}" if len(parts) > 1 else parts[0]
        role = row.get("role", "?")
        content = row.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."

        prefix = "USER" if role == "user" else "ASST"
        lines.append(f"**[{ts}] {prefix}:**")
        lines.append(f"{content}\n")

    return "\n".join(lines)


def format_sessions_list(rows: list[dict]) -> str | None:
    if not rows:
        return None

    lines = ["## Recent Sessions\n"]
    for r in rows:
        sid = (r.get("session_id") or "unknown")[:8]
        proj = r.get("project") or "no-project"
        machine = r.get("machine") or ""
        first = (r.get("first_msg") or "")[:10]
        last = (r.get("last_msg") or "")[:10]
        count = r.get("msg_count", 0)
        snippets = r.get("snippets") or ""
        # Take first user snippet only
        snippet = snippets.split(" | ")[0][:80] if snippets else ""

        machine_tag = f" [{machine}]" if machine else ""
        date_range = f"{first}" if first == last else f"{first} to {last}"
        lines.append(f"- **{sid}**{machine_tag} {proj} ({date_range}, {count} msgs)")
        if snippet:
            lines.append(f"  > {snippet}")

    return "\n".join(lines)


def generate_context(args: argparse.Namespace) -> None:
    """Generate a context block combining facts, recent activity, and relevant memories."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    sections: list[str] = []

    # 1. High-confidence facts
    try:
        fact_sql = """
            SELECT fact, category, project, timestamp FROM facts
            WHERE confidence >= 0.5
        """
        fact_params: list = []
        if args.project:
            fact_sql += " AND project LIKE ?"
            fact_params.append(f"%{args.project}%")
        fact_sql += " ORDER BY confidence DESC, timestamp DESC LIMIT 20"
        fact_rows = conn.execute(fact_sql, fact_params).fetchall()

        if fact_rows:
            lines = ["## Known Facts"]
            for r in fact_rows:
                lines.append(f"- [{r['category']}] {r['fact']}")
            sections.append("\n".join(lines))
    except sqlite3.OperationalError:
        pass

    # 2. Recent sessions summary
    try:
        session_sql = """
            SELECT session_id, project, MAX(timestamp) as last_ts, COUNT(*) as msgs
            FROM messages
            WHERE session_id IS NOT NULL
        """
        s_params: list = []
        if args.project:
            session_sql += " AND project LIKE ?"
            s_params.append(f"%{args.project}%")
        session_sql += " GROUP BY session_id ORDER BY last_ts DESC LIMIT 5"
        session_rows = conn.execute(session_sql, s_params).fetchall()

        if session_rows:
            lines = ["## Recent Sessions"]
            for r in session_rows:
                ts = (r["last_ts"] or "")[:10]
                proj = r["project"] or "general"
                lines.append(f"- [{ts}] {proj} ({r['msgs']} messages)")
            sections.append("\n".join(lines))
    except sqlite3.OperationalError:
        pass

    # 3. Top entities
    try:
        ent_sql = """
            SELECT name, entity_type, mention_count, last_seen
            FROM entities WHERE mention_count >= 3 AND id > 0
        """
        ent_sql += " ORDER BY mention_count DESC LIMIT 15"
        ent_rows = conn.execute(ent_sql, []).fetchall()

        if ent_rows:
            lines = ["## Frequently Used"]
            for r in ent_rows:
                lines.append(f"- {r['name']} ({r['entity_type']}, {r['mention_count']}x)")
            sections.append("\n".join(lines))
    except sqlite3.OperationalError:
        pass

    # 4. Query-specific results
    if args.query:
        query = " ".join(args.query)
        try:
            results = search_fts(query, project=args.project, limit=5)
            if results:
                lines = [f"## Relevant Memories for \"{query}\""]
                for r in results:
                    ts = (r.get("timestamp") or "")[:10]
                    content = r.get("content", "")[:200]
                    lines.append(f"- [{ts}] ({r.get('role', '?')}) {content}")
                sections.append("\n".join(lines))
        except Exception:
            pass

    conn.close()

    if sections:
        output = "\n\n".join(sections)
        # Rough token estimation (1 token ~ 4 chars)
        max_chars = args.max_tokens * 4
        if len(output) > max_chars:
            output = output[:max_chars] + "\n\n[...truncated]"
        print(output)
    else:
        print("No context available.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Claude Code conversation memory",
        usage="claude-recall <query> [options]"
    )
    sub = parser.add_subparsers(dest="command")

    # Default: search (keyword + optional semantic)
    search = sub.add_parser("search", aliases=["s"], help="Search messages")
    search.add_argument("query", nargs="+", help="Search terms")
    search.add_argument("--project", "-p", help="Filter by project name")
    search.add_argument("--since", "-s", help="Only results after this date (ISO 8601)")
    search.add_argument("--limit", "-n", type=int, default=5, help="Max results (default: 5)")
    search.add_argument("--role", "-r", choices=["user", "assistant"], help="Filter by role")
    search.add_argument("--semantic", "-S", action="store_true", help="Use semantic (vector) search")
    search.add_argument("--both", "-B", action="store_true",
                        help="Run both keyword and semantic search")
    search.add_argument("--no-decay", action="store_true", help="Disable temporal recency boost")

    # Session commands
    session = sub.add_parser("session", aliases=["ss"], help="View a session's messages")
    session.add_argument("session_id", help="Session ID (or prefix)")
    session.add_argument("--limit", "-n", type=int, default=50)

    sessions = sub.add_parser("sessions", aliases=["ls"], help="List recent sessions")
    sessions.add_argument("--project", "-p")
    sessions.add_argument("--since", "-s")
    sessions.add_argument("--limit", "-n", type=int, default=20)

    # Facts
    facts = sub.add_parser("facts", aliases=["f"], help="Search extracted facts")
    facts.add_argument("query", nargs="+", help="Search terms")
    facts.add_argument("--project", "-p")
    facts.add_argument("--category", "-c",
                        choices=["preference", "decision", "learning", "context", "tool", "pattern"])
    facts.add_argument("--limit", "-n", type=int, default=10)

    # Context injection
    ctx = sub.add_parser("context", aliases=["ctx"], help="Generate context block for session injection")
    ctx.add_argument("query", nargs="*", help="Optional focus query")
    ctx.add_argument("--project", "-p")
    ctx.add_argument("--max-tokens", type=int, default=2000,
                     help="Approximate max output size in tokens")

    args = parser.parse_args()

    if args.command in ("search", "s"):
        query = " ".join(args.query)
        decay = 0 if args.no_decay else 30

        if args.both:
            # Run both and interleave
            fts_results = search_fts(query, project=args.project, since=args.since,
                                     limit=args.limit, role=args.role)
            sem_results = search_semantic(query, project=args.project, since=args.since,
                                         limit=args.limit, role=args.role, decay=decay)
            output = format_message_results(fts_results, query, "fts")
            if output:
                print(output)
            output = format_message_results(sem_results, query, "semantic")
            if output:
                print(output)
            if not fts_results and not sem_results:
                print(f"No results found for \"{query}\"")
                sys.exit(1)

        elif args.semantic:
            results = search_semantic(query, project=args.project, since=args.since,
                                      limit=args.limit, role=args.role, decay=decay)
            output = format_message_results(results, query, "semantic")
            if output:
                print(output)
            else:
                print(f"No results found for \"{query}\"")
                sys.exit(1)
        else:
            results = search_fts(query, project=args.project, since=args.since,
                                 limit=args.limit, role=args.role)
            output = format_message_results(results, query, "fts")
            if output:
                print(output)
            else:
                print(f"No results found for \"{query}\"")
                sys.exit(1)

    elif args.command in ("session", "ss"):
        rows = get_session(args.session_id, limit=args.limit)
        output = format_session(rows)
        if output:
            print(output)
        else:
            print(f"No messages found for session \"{args.session_id}\"")
            sys.exit(1)

    elif args.command in ("sessions", "ls"):
        rows = list_sessions(project=args.project, since=args.since, limit=args.limit)
        output = format_sessions_list(rows)
        if output:
            print(output)
        else:
            print("No sessions found.")
            sys.exit(1)

    elif args.command in ("facts", "f"):
        query = " ".join(args.query)
        rows = search_facts(query, project=args.project, category=args.category,
                            limit=args.limit)
        output = format_facts_results(rows, query)
        if output:
            print(output)
        else:
            print(f"No facts found for \"{query}\"")
            sys.exit(1)

    elif args.command in ("context", "ctx"):
        generate_context(args)

    else:
        parser.print_help()


KNOWN_COMMANDS = {"search", "s", "session", "ss", "sessions", "ls",
                  "facts", "f", "context", "ctx", "-h", "--help"}


def cli() -> None:
    """Entry point that handles bare 'claude-recall <query>' syntax."""
    if len(sys.argv) > 1 and sys.argv[1] not in KNOWN_COMMANDS:
        # Bare query: inject 'search' subcommand
        sys.argv.insert(1, "search")
    main()


if __name__ == "__main__":
    cli()
