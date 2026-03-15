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

try:
    from src.memory_db import get_conn
except ImportError:
    from memory_db import get_conn

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


def _build_facts_section(conn, project: str | None, now: datetime, limit: int = 30) -> str:
    """Build the Key Facts section, returning the section text (or empty string)."""
    sql = """SELECT DISTINCT fact, category, project, confidence,
                    last_validated, timestamp
             FROM facts WHERE confidence >= 0.5"""
    params: list = []
    if project:
        sql += " AND (project LIKE ? OR project IS NULL)"
        params.append(f"%{project}%")
    else:
        sql += """ AND (project IS NULL
                    OR project LIKE '/home/%'
                    OR project LIKE '/Users/%')"""
    sql += " ORDER BY confidence DESC, timestamp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        return ""

    # Apply time-based decay: -0.2 per 90 days unvalidated, min 0.1
    decayed: list[tuple[float, sqlite3.Row]] = []
    for r in rows:
        effective_conf: float = r["confidence"]
        ref_time = r["last_validated"] or r["timestamp"]
        if ref_time:
            try:
                ref_dt = datetime.fromisoformat(ref_time.replace("Z", "+00:00"))
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                days_old = (now - ref_dt).days
                if days_old > 90:
                    periods = days_old // 90
                    effective_conf = max(0.1, effective_conf - 0.2 * periods)
            except (ValueError, TypeError):
                pass
        decayed.append((effective_conf, r))

    # Re-sort by effective confidence and take top entries
    decayed.sort(key=lambda x: x[0], reverse=True)
    top = min(15, limit)
    decayed = [(c, r) for c, r in decayed if c >= 0.1][:top]

    if not decayed:
        return ""

    lines = ["## Key Facts"]
    for _, r in decayed:
        proj_tag = f" ({r['project']})" if r["project"] and not project else ""
        lines.append(f"- **[{r['category']}]** {r['fact']}{proj_tag}")
    return "\n".join(lines)


def _build_sessions_section(conn, project: str | None, week_ago: str) -> str:
    """Build the Recent Sessions section."""
    sql = """
        SELECT session_id, project, machine,
               MIN(timestamp) as started,
               MAX(timestamp) as ended,
               COUNT(*) as msg_count
        FROM messages
        WHERE timestamp >= ?
    """
    params: list = [week_ago]
    if project:
        sql += " AND project LIKE ?"
        params.append(f"%{project}%")
    sql += " GROUP BY session_id ORDER BY ended DESC LIMIT 8"
    rows = conn.execute(sql, params).fetchall()

    # If project-filtered and few results, also show other recent sessions
    other_rows: list = []
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

    if not rows and not other_rows:
        return ""

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
    return "\n".join(lines)


def _build_entities_section(conn, month_ago: str) -> str:
    """Build the Active Stack (entities) section."""
    sql = """
        SELECT name, entity_type, mention_count
        FROM entities
        WHERE mention_count >= 3 AND id > 0 AND last_seen >= ?
        ORDER BY mention_count DESC LIMIT 20
    """
    rows = conn.execute(sql, (month_ago,)).fetchall()

    if not rows:
        return ""

    by_type: dict[str, list] = {}
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
    has_content = False
    for etype, label in type_labels.items():
        items = by_type.get(etype, [])
        if items:
            names = ", ".join(f"{r['name']}({r['mention_count']})" for r in items[:6])
            lines.append(f"- **{label}**: {names}")
            has_content = True

    return "\n".join(lines) if has_content else ""


def _build_focus_section(conn, focus: str) -> str:
    """Build the focus-specific recall section."""
    fts_rows = conn.execute("""
        SELECT m.content, m.role, m.timestamp, m.project
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH ?
        ORDER BY messages_fts.rank LIMIT 5
    """, (focus,)).fetchall()

    if not fts_rows:
        return ""

    lines = [f"## Relevant to: \"{focus}\""]
    for r in fts_rows:
        ts = (r["timestamp"] or "")[:10]
        content = r["content"][:200]
        if len(r["content"]) > 200:
            content += "..."
        lines.append(f"- [{ts}] ({r['role']}) {content}")
    return "\n".join(lines)


def generate_memory_context(project=None, focus=None, max_tokens=2000, auto_detect=True):
    """Generate a markdown context block summarizing relevant memory.

    Sections are built independently and assembled in priority order within
    the token budget (estimated as len // 4).  Sections that don't fit are
    skipped entirely — no mid-text truncation.

    Priority order: Header (1) > Key Facts (2) > Focus recall (3)
                  > Recent Sessions (4) > Active Stack (5)
    """
    if project is None and auto_detect:
        project = detect_project_from_cwd()

    conn = get_conn(str(DB_PATH))

    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()

    budget_chars = max_tokens * 4

    # -- Header (priority 1, always included) ----------------------------------
    project_label = f" ({project})" if project else ""
    header = f"# Memory Context{project_label}\n"

    # -- Build each section independently with its priority ---------------------
    # List of (priority, section_text) — only non-empty sections are appended
    prioritized_sections: list[tuple[int, str]] = []

    # Priority 2: Key Facts
    try:
        facts_text = _build_facts_section(conn, project, now, limit=30)
        if facts_text:
            # If facts section alone exceeds budget, try progressively smaller limits
            if len(facts_text) // 4 > max_tokens:
                for reduced_limit in (10, 5):
                    facts_text = _build_facts_section(conn, project, now, limit=reduced_limit)
                    if not facts_text or len(facts_text) // 4 <= max_tokens:
                        break
            if facts_text:
                prioritized_sections.append((2, facts_text))
    except sqlite3.OperationalError as e:
        print(f"Warning: {e}", file=sys.stderr)

    # Priority 3: Focus-specific recall (only when --focus is given)
    if focus:
        try:
            focus_text = _build_focus_section(conn, focus)
            if focus_text:
                prioritized_sections.append((3, focus_text))
        except sqlite3.OperationalError as e:
            print(f"Warning: {e}", file=sys.stderr)

    # Priority 4: Recent Sessions
    try:
        sessions_text = _build_sessions_section(conn, project, week_ago)
        if sessions_text:
            prioritized_sections.append((4, sessions_text))
    except sqlite3.OperationalError as e:
        print(f"Warning: {e}", file=sys.stderr)

    # Priority 5: Active Stack (entities)
    try:
        entities_text = _build_entities_section(conn, month_ago)
        if entities_text:
            prioritized_sections.append((5, entities_text))
    except sqlite3.OperationalError as e:
        print(f"Warning: {e}", file=sys.stderr)

    conn.close()

    # -- Priority-based assembly within budget ---------------------------------
    assembled = header
    used = len(header)

    for _priority, section_text in sorted(prioritized_sections):
        section_cost = len(section_text) + 2  # +2 for "\n\n" separator
        if used + section_cost <= budget_chars:
            assembled += "\n\n" + section_text
            used += section_cost
        # else: skip this section — doesn't fit in the token budget

    if len(assembled) <= len(header) + 5:
        return "# Memory Context\n\nNo memory data available yet."

    return assembled


def main():
    parser = argparse.ArgumentParser(description="Generate memory context for Claude Code sessions")
    parser.add_argument("--project", "-p", help="Focus on a specific project (auto-detected from $PWD if omitted)")
    parser.add_argument("--focus", "-f", help="Additional focus query for relevant recall")
    parser.add_argument("--max-tokens", "-t", type=int, default=2000,
                        help="Approximate max output tokens (default: 2000)")
    parser.add_argument("--output", "-o", help="Write to file instead of stdout")
    parser.add_argument("--stdout", action="store_true",
                        help="Output to stdout instead of writing to file (for hook use)")
    parser.add_argument("--no-detect", action="store_true",
                        help="Disable auto-detection of project from $PWD")

    args = parser.parse_args()
    output = generate_memory_context(
        project=args.project,
        focus=args.focus,
        max_tokens=args.max_tokens,
        auto_detect=not args.no_detect,
    )

    if args.stdout:
        print(output)
    elif args.output:
        Path(args.output).write_text(output)
        print(f"Context written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
