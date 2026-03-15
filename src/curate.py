#!/usr/bin/env python3
"""Fact curation tool — review, approve, edit, and add hand-curated facts.

Designed for quick periodic review sessions. Run:
    python3 curate.py review          # Review auto-extracted facts, approve/reject
    python3 curate.py add             # Add a new hand-curated fact interactively
    python3 curate.py import facts.md # Bulk import from a markdown file
    python3 curate.py export          # Export curated facts as markdown
"""

import argparse
import sqlite3
import sys
from pathlib import Path

try:
    from src.memory_db import get_conn
except ImportError:
    from memory_db import get_conn

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"
CURATE_FILE = MEMORY_DIR / "curated-facts.md"

CATEGORIES = ["preference", "decision", "learning", "context", "tool", "pattern"]


def review_facts(min_confidence=0.0, max_confidence=0.89, limit=20, category=None):
    """Interactive review of auto-extracted facts. Approve, reject, or edit."""
    conn = get_conn(str(DB_PATH))

    sql = "SELECT * FROM facts WHERE confidence > ? AND confidence <= ?"
    params = [min_confidence, max_confidence]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("No facts to review in that range.")
        conn.close()
        return

    approved = 0
    rejected = 0
    edited = 0

    print(f"\n  Reviewing {len(rows)} facts. Commands:")
    print("    y = approve (set confidence 1.0)")
    print("    n = reject (delete)")
    print("    e = edit the fact text")
    print("    s = skip")
    print("    q = quit\n")

    for r in rows:
        ts = (r["timestamp"] or "unknown")[:10]
        proj = r["project"] or "general"
        print(f"  [{ts}] [{r['category']}] (conf={r['confidence']:.1f}) {proj}")
        print(f"  {r['fact']}")
        print()

        while True:
            choice = input("  [y/n/e/s/q] > ").strip().lower()
            if choice == "y":
                conn.execute("UPDATE facts SET confidence = 1.0 WHERE id = ?", (r["id"],))
                conn.commit()
                approved += 1
                print("  -> Approved\n")
                break
            elif choice == "n":
                conn.execute("DELETE FROM facts WHERE id = ?", (r["id"],))
                conn.commit()
                rejected += 1
                print("  -> Rejected\n")
                break
            elif choice == "e":
                new_text = input("  New fact text: ").strip()
                if new_text:
                    conn.execute("UPDATE facts SET fact = ?, confidence = 1.0 WHERE id = ?",
                                 (new_text, r["id"]))
                    conn.commit()
                    edited += 1
                    print("  -> Edited & approved\n")
                break
            elif choice == "s":
                print("  -> Skipped\n")
                break
            elif choice == "q":
                print(f"\n  Session: {approved} approved, {rejected} rejected, {edited} edited")
                conn.close()
                return
            else:
                print("  Invalid choice. Use y/n/e/s/q")

    print(f"\n  Session: {approved} approved, {rejected} rejected, {edited} edited")
    conn.close()


def add_fact_interactive():
    """Add a hand-curated fact interactively."""
    print("\n  Add a new curated fact")
    print(f"  Categories: {', '.join(CATEGORIES)}\n")

    fact = input("  Fact: ").strip()
    if not fact:
        print("  Cancelled.")
        return

    while True:
        category = input(f"  Category [{'/'.join(CATEGORIES)}]: ").strip().lower()
        if category in CATEGORIES:
            break
        print(f"  Invalid. Choose from: {', '.join(CATEGORIES)}")

    project = input("  Project (or Enter for general): ").strip() or None

    conn = get_conn(str(DB_PATH))
    conn.execute(
        "INSERT INTO facts (fact, category, confidence, project) VALUES (?, ?, 1.0, ?)",
        (fact, category, project),
    )
    conn.commit()
    conn.close()
    print(f"  -> Added with confidence 1.0\n")


def import_facts(filepath):
    """Import facts from a markdown file.

    Expected format:
    ## preference
    - I prefer Python over TypeScript
    - I like simple, minimal code

    ## context
    - I'm based in San Francisco
    - I own Jaxx Winery
    """
    path = Path(filepath)
    if not path.exists():
        # If the file doesn't exist, create a template
        template = """# Curated Facts
# Add facts below under category headers. Run: python3 curate.py import curated-facts.md

## preference
- I prefer Python for most projects
- I like simple, minimal code over frameworks

## context
- I'm based in San Francisco
- I own Jaxx Winery

## decision

## tool
- I use Pushover for notifications
- I use Tailscale for networking between all machines

## learning

## pattern
"""
        path.write_text(template)
        print(f"  Created template at {filepath}")
        print(f"  Edit it, then run: python3 curate.py import {filepath}")
        return

    text = path.read_text()
    conn = get_conn(str(DB_PATH))

    current_category = None
    imported = 0

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            cat = line[3:].strip().lower()
            if cat in CATEGORIES:
                current_category = cat
            continue
        if line.startswith("- ") and current_category:
            fact = line[2:].strip()
            if fact and not fact.startswith("#"):
                conn.execute(
                    """INSERT OR REPLACE INTO facts (fact, category, confidence, project)
                       VALUES (?, ?, 1.0, NULL)""",
                    (fact, current_category),
                )
                imported += 1

    conn.commit()
    conn.close()
    print(f"  Imported {imported} curated facts from {filepath}")


def export_facts(min_confidence=0.9):
    """Export high-confidence facts as markdown."""
    conn = get_conn(str(DB_PATH))
    rows = conn.execute(
        "SELECT fact, category, project FROM facts WHERE confidence >= ? ORDER BY category, project",
        (min_confidence,),
    ).fetchall()
    conn.close()

    if not rows:
        print("No facts above that confidence threshold.")
        return

    by_cat = {}
    for fact, cat, proj in rows:
        by_cat.setdefault(cat, []).append((fact, proj))

    lines = ["# Curated Facts\n"]
    for cat in CATEGORIES:
        items = by_cat.get(cat, [])
        if items:
            lines.append(f"## {cat}")
            for fact, proj in items:
                proj_tag = f" ({proj})" if proj else ""
                lines.append(f"- {fact}{proj_tag}")
            lines.append("")

    output = "\n".join(lines)
    print(output)

    # Also write to file
    CURATE_FILE.write_text(output)
    print(f"\n  (Also written to {CURATE_FILE})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Claude Memory Fact Curation")
    sub = parser.add_subparsers(dest="command")

    rev = sub.add_parser("review", help="Interactively review auto-extracted facts")
    rev.add_argument("--category", "-c", choices=CATEGORIES)
    rev.add_argument("--limit", "-n", type=int, default=20)
    rev.add_argument("--all", action="store_true", help="Review all facts, not just unreviewed")

    sub.add_parser("add", help="Add a new curated fact interactively")

    imp = sub.add_parser("import", help="Import facts from a markdown file")
    imp.add_argument("file", nargs="?", default=str(CURATE_FILE), help="Markdown file path")

    exp = sub.add_parser("export", help="Export high-confidence facts as markdown")
    exp.add_argument("--min-confidence", type=float, default=0.9)

    stats = sub.add_parser("stats", help="Show fact confidence distribution")

    args = parser.parse_args()

    if args.command == "review":
        max_conf = 1.1 if args.all else 0.89
        review_facts(category=args.category, limit=args.limit, max_confidence=max_conf)
    elif args.command == "add":
        add_fact_interactive()
    elif args.command == "import":
        import_facts(args.file)
    elif args.command == "export":
        export_facts(min_confidence=args.min_confidence)
    elif args.command == "stats":
        conn = get_conn(str(DB_PATH))
        total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        curated = conn.execute("SELECT COUNT(*) FROM facts WHERE confidence = 1.0").fetchone()[0]
        llm = conn.execute("SELECT COUNT(*) FROM facts WHERE confidence >= 0.8 AND confidence < 1.0").fetchone()[0]
        heuristic = conn.execute("SELECT COUNT(*) FROM facts WHERE confidence > 0 AND confidence < 0.8").fetchone()[0]
        sentinel = conn.execute("SELECT COUNT(*) FROM facts WHERE confidence = 0").fetchone()[0]
        print(f"  Total facts:    {total}")
        print(f"  Hand-curated:   {curated} (confidence = 1.0)")
        print(f"  LLM-extracted:  {llm} (confidence 0.8-0.9)")
        print(f"  Heuristic:      {heuristic} (confidence < 0.8)")
        print(f"  Sentinels:      {sentinel} (placeholders)")
        conn.close()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
