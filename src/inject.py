#!/usr/bin/env python3
"""Context injection — automatically generate memory context for Claude Code sessions.

Project-aware: detects the current working directory and filters context to the
most relevant project. Falls back to a general summary when not in a known project.

Usage in cron:
    python3 inject.py -o ~/.claude/memory-context.md

On-demand with project focus:
    python3 inject.py --project kalshi --focus "websocket debugging"

Auto-detect from $PWD:
    cd ~/kalshi-forecast && python3 ~/claude-memory/inject.py
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"

# Map directory names/keywords to project filter strings used in the DB
PROJECT_ALIASES = {
    "kalshi-forecast": "kalshi",
    "kalshi": "kalshi",
    "Kalshi": "kalshi",
    "arena-detector-api": "kalshi",
    "tribunal-nli": "tribunal",
    "tribunal": "tribunal",
    "conclave": "tribunal",
    "ivanhoff-trading-bot": "ivanhoff",
    "ivanhoff": "ivanhoff",
    "freshell": "freshell",
    "flooriq": "flooriq",
    "claude-memory": "claude-memory",
}


def detect_project_from_cwd():
    """Infer project context from $PWD."""
    cwd = os.environ.get("PWD", os.getcwd())
    parts = Path(cwd).parts

    # Walk from deepest to shallowest looking for a known project dir
    for part in reversed(parts):
        if part in PROJECT_ALIASES:
            return PROJECT_ALIASES[part]

    # Try matching against the full path
    cwd_lower = cwd.lower()
    for alias, project in PROJECT_ALIASES.items():
        if alias.lower() in cwd_lower:
            return project

    return None


def generate_memory_context(project=None, focus=None, max_tokens=2000, auto_detect=True):
    """Generate a markdown context block summarizing relevant memory."""
    if project is None and auto_detect:
        project = detect_project_from_cwd()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    sections = []

    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()

    project_label = f" ({project})" if project else ""
    sections.append(f"# Memory Context{project_label}\n")

    # Section 1: High-value facts (project-filtered if applicable)
    try:
        sql = "SELECT DISTINCT fact, category, project FROM facts WHERE confidence >= 0.5"
        params = []
        if project:
            sql += " AND (project LIKE ? OR project IS NULL)"
            params.append(f"%{project}%")
        else:
            # Global context: exclude facts tagged to specific projects like /kalshi
            # Only show general facts, curated facts, and facts from generic project paths
            sql += """ AND (project IS NULL
                        OR project LIKE '/home/%'
                        OR project LIKE '/Users/%')"""
        sql += " ORDER BY confidence DESC, timestamp DESC LIMIT 15"
        rows = conn.execute(sql, params).fetchall()

        if rows:
            lines = ["## Key Facts"]
            for r in rows:
                proj_tag = f" ({r['project']})" if r["project"] and not project else ""
                lines.append(f"- **[{r['category']}]** {r['fact']}{proj_tag}")
            sections.append("\n".join(lines))
    except sqlite3.OperationalError:
        pass

    # Section 2: Recent sessions (project-filtered, then general)
    try:
        sql = """
            SELECT session_id, project, machine,
                   MIN(timestamp) as started,
                   MAX(timestamp) as ended,
                   COUNT(*) as msg_count
            FROM messages
            WHERE timestamp >= ?
        """
        params = [week_ago]
        if project:
            sql += " AND project LIKE ?"
            params.append(f"%{project}%")
        sql += " GROUP BY session_id ORDER BY ended DESC LIMIT 8"
        rows = conn.execute(sql, params).fetchall()

        # If project-filtered and few results, also show other recent sessions
        other_rows = []
        if project and len(rows) < 5:
            other_sql = """
                SELECT session_id, project, machine,
                       MIN(timestamp) as started, MAX(timestamp) as ended,
                       COUNT(*) as msg_count
                FROM messages
                WHERE timestamp >= ?
                AND (project NOT LIKE ? OR project IS NULL)
                GROUP BY session_id ORDER BY ended DESC LIMIT 5
            """
            other_rows = conn.execute(other_sql, [week_ago, f"%{project}%"]).fetchall()

        if rows or other_rows:
            lines = ["## Recent Sessions (last 7 days)"]
            for r in list(rows) + list(other_rows):
                started = (r["started"] or "")[:16].replace("T", " ")
                proj = r["project"] or "general"
                topic_row = conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY timestamp LIMIT 1",
                    (r["session_id"],),
                ).fetchone()
                topic = ""
                if topic_row:
                    topic = topic_row[0][:100]
                    if len(topic_row[0]) > 100:
                        topic += "..."
                lines.append(f"- [{started}] {proj} ({r['msg_count']} msgs): {topic}")
            sections.append("\n".join(lines))
    except sqlite3.OperationalError:
        pass

    # Section 3: Relevant entities (project-aware grouping)
    try:
        sql = """
            SELECT name, entity_type, mention_count
            FROM entities
            WHERE mention_count >= 3 AND id > 0 AND last_seen >= ?
            ORDER BY mention_count DESC LIMIT 20
        """
        rows = conn.execute(sql, (month_ago,)).fetchall()

        if rows:
            # Group by type
            by_type = {}
            for r in rows:
                etype = r["entity_type"]
                by_type.setdefault(etype, []).append(r)

            lines = ["## Active Stack"]
            type_labels = {
                "language": "Languages", "library": "Libraries",
                "platform": "Platforms", "ai_service": "AI Services",
                "infrastructure": "Infrastructure", "database": "Databases",
                "tool": "Tools", "protocol": "Protocols", "os": "OS",
                "service": "Services", "file": "Key Files",
            }
            for etype, label in type_labels.items():
                items = by_type.get(etype, [])
                if items:
                    names = ", ".join(f"{r['name']}({r['mention_count']})" for r in items[:6])
                    lines.append(f"- **{label}**: {names}")
            sections.append("\n".join(lines))
    except sqlite3.OperationalError:
        pass

    # Section 4: Focus-specific recall
    if focus:
        try:
            fts_rows = conn.execute("""
                SELECT m.content, m.role, m.timestamp, m.project
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.rowid
                WHERE messages_fts MATCH ?
                ORDER BY messages_fts.rank LIMIT 5
            """, (focus,)).fetchall()

            if fts_rows:
                lines = [f"## Relevant to: \"{focus}\""]
                for r in fts_rows:
                    ts = (r["timestamp"] or "")[:10]
                    content = r["content"][:200]
                    if len(r["content"]) > 200:
                        content += "..."
                    lines.append(f"- [{ts}] ({r['role']}) {content}")
                sections.append("\n".join(lines))
        except sqlite3.OperationalError:
            pass

    conn.close()

    if len(sections) <= 1:
        return "# Memory Context\n\nNo memory data available yet."

    output = "\n\n".join(sections)

    max_chars = max_tokens * 4
    if len(output) > max_chars:
        output = output[:max_chars] + "\n\n[...truncated to fit token budget]"

    return output


def main():
    parser = argparse.ArgumentParser(description="Generate memory context for Claude Code sessions")
    parser.add_argument("--project", "-p", help="Focus on a specific project (auto-detected from $PWD if omitted)")
    parser.add_argument("--focus", "-f", help="Additional focus query for relevant recall")
    parser.add_argument("--max-tokens", "-t", type=int, default=2000,
                        help="Approximate max output tokens (default: 2000)")
    parser.add_argument("--output", "-o", help="Write to file instead of stdout")
    parser.add_argument("--no-detect", action="store_true",
                        help="Disable auto-detection of project from $PWD")

    args = parser.parse_args()
    output = generate_memory_context(
        project=args.project,
        focus=args.focus,
        max_tokens=args.max_tokens,
        auto_detect=not args.no_detect,
    )

    if args.output:
        Path(args.output).write_text(output)
        print(f"Context written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
