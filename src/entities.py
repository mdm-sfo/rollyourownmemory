#!/usr/bin/env python3
"""Entity extraction — identify and track projects, libraries, tools, and concepts
mentioned across conversations."""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

try:
    from src.memory_db import get_conn
except ImportError:
    from memory_db import get_conn

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"

# Typed entity dictionaries — entity_name -> entity_type
KNOWN_ENTITIES = {
    # Languages
    "python": "language", "javascript": "language", "typescript": "language",
    "rust": "language", "go": "language", "ruby": "language", "java": "language",
    "c++": "language", "sql": "language", "bash": "language",

    # Frameworks & libraries
    "react": "library", "vue": "library", "angular": "library", "svelte": "library",
    "nextjs": "library", "next.js": "library", "nuxt": "library",
    "fastapi": "library", "flask": "library", "django": "library",
    "express": "library", "koa": "library",
    "pytorch": "library", "tensorflow": "library", "numpy": "library",
    "pandas": "library", "scikit-learn": "library", "scipy": "library",
    "sentence-transformers": "library", "chromadb": "library",
    "faiss": "library", "pinecone": "library", "weaviate": "library",
    "streamlit": "library", "plotly": "library", "matplotlib": "library",

    # Databases & data stores
    "sqlite": "database", "postgres": "database", "postgresql": "database",
    "mysql": "database", "redis": "database", "mongodb": "database",
    "dynamodb": "database",

    # Infrastructure & devops
    "docker": "infrastructure", "kubernetes": "infrastructure",
    "k8s": "infrastructure", "terraform": "infrastructure",
    "ansible": "infrastructure", "nginx": "infrastructure",
    "systemd": "infrastructure", "tailscale": "infrastructure",

    # Cloud & platforms
    "aws": "platform", "gcp": "platform", "azure": "platform",
    "vercel": "platform", "cloudflare": "platform", "heroku": "platform",
    "github": "platform", "gitlab": "platform", "bitbucket": "platform",

    # AI models & services
    "openai": "ai_service", "anthropic": "ai_service", "claude": "ai_service",
    "gpt": "ai_service", "llama": "ai_service", "ollama": "ai_service",
    "vllm": "ai_service", "perplexity": "ai_service", "timesfm": "ai_service",

    # Dev tools
    "git": "tool", "pytest": "tool", "jest": "tool", "vitest": "tool",
    "mocha": "tool", "cypress": "tool", "playwright": "tool",
    "webpack": "tool", "vite": "tool", "esbuild": "tool",
    "pip": "tool", "npm": "tool", "yarn": "tool", "pnpm": "tool",
    "cargo": "tool", "conda": "tool",

    # Operating systems
    "linux": "os", "ubuntu": "os", "debian": "os", "macos": "os", "windows": "os",

    # CLI & protocols
    "ssh": "protocol", "rsync": "protocol", "http": "protocol", "websocket": "protocol",
    "tmux": "tool", "vim": "tool", "neovim": "tool", "vscode": "tool",

    # User-specific platforms
    "kalshi": "platform", "polymarket": "platform", "alpaca": "platform",
}

# Patterns for extracting entities from text
LIBRARY_PATTERN = re.compile(
    r'(?:pip install|npm install|cargo add|brew install|apt install|import|from|require\()'
    r'\s+["\']?([a-zA-Z][a-zA-Z0-9._-]{2,40})',
    re.IGNORECASE,
)

# Common words that match patterns but aren't entities
STOP_WORDS = {
    "the", "and", "for", "from", "with", "this", "that", "what", "where", "when",
    "how", "why", "which", "there", "here", "have", "been", "will", "would", "could",
    "should", "about", "into", "over", "after", "before", "between", "through",
    "under", "above", "below", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "only", "own", "same", "than", "too", "very",
    "just", "because", "but", "not", "also", "then", "first", "last", "next",
    "new", "old", "great", "little", "right", "big", "long", "small", "large",
    "good", "bad", "high", "low", "early", "late", "young", "important",
    "different", "public", "able", "available", "previous", "sure", "try",
    "like", "still", "already", "since", "while", "back", "even", "well",
    "way", "may", "say", "help", "get", "got", "put", "make", "made", "take",
    "let", "keep", "give", "work", "call", "need", "see", "look", "find",
    "know", "want", "tell", "ask", "use", "run", "set", "show", "turn",
    "move", "change", "play", "pay", "close", "open", "start", "stop",
    "yes", "now", "out", "all", "any", "off", "end", "feb", "mar", "jan",
    "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "etc",
    "can", "did", "does", "had", "has", "was", "were", "doing", "much",
    "many", "something", "anything", "everything", "nothing", "using",
    "going", "running", "getting", "looking", "trying", "working",
}

FILE_PATTERN = re.compile(
    r'(?:^|\s)([a-zA-Z0-9_./\-]+\.(?:py|js|ts|tsx|jsx|rs|go|sql|sh|yaml|yml|json|toml|md|css|html))\b'
)

SERVICE_PATTERN = re.compile(
    r'(?:https?://)?([a-z0-9-]+\.(?:com|io|dev|ai|org|net|co))\b',
    re.IGNORECASE,
)


def extract_entities_from_text(text, message_id, session_id, timestamp):
    """Extract entities from a single message text."""
    entities = []
    text_lower = text.lower()

    # Known entities with proper type classification
    for name, etype in KNOWN_ENTITIES.items():
        if re.search(r'\b' + re.escape(name) + r'\b', text_lower):
            entities.append({
                "name": name,
                "entity_type": etype,
                "message_id": message_id,
                "session_id": session_id,
                "timestamp": timestamp,
            })

    # Library imports / installs
    for match in LIBRARY_PATTERN.finditer(text):
        name = match.group(1).strip("\"'").lower()
        if len(name) > 2 and name not in STOP_WORDS:
            entities.append({
                "name": name,
                "entity_type": "library",
                "message_id": message_id,
                "session_id": session_id,
                "timestamp": timestamp,
            })

    # File paths
    for match in FILE_PATTERN.finditer(text):
        path = match.group(1)
        if len(path) > 3:
            entities.append({
                "name": path,
                "entity_type": "file",
                "message_id": message_id,
                "session_id": session_id,
                "timestamp": timestamp,
            })

    # Services / domains
    for match in SERVICE_PATTERN.finditer(text):
        domain = match.group(1).lower()
        if domain not in {"example.com", "localhost.com"}:
            entities.append({
                "name": domain,
                "entity_type": "service",
                "message_id": message_id,
                "session_id": session_id,
                "timestamp": timestamp,
            })

    return entities


def upsert_entity(conn, name, entity_type, timestamp):
    """Insert or update an entity, incrementing mention count."""
    existing = conn.execute(
        "SELECT id, first_seen, mention_count FROM entities WHERE name = ? AND entity_type = ?",
        (name, entity_type),
    ).fetchone()

    if existing:
        entity_id, first_seen, count = existing
        conn.execute(
            "UPDATE entities SET mention_count = ?, last_seen = ? WHERE id = ?",
            (count + 1, timestamp, entity_id),
        )
        return entity_id
    else:
        cur = conn.execute(
            "INSERT INTO entities (name, entity_type, first_seen, last_seen, mention_count) VALUES (?, ?, ?, ?, 1)",
            (name, entity_type, timestamp, timestamp),
        )
        return cur.lastrowid


def store_mention(conn, entity_id, message_id, session_id, timestamp):
    conn.execute(
        "INSERT OR IGNORE INTO entity_mentions (entity_id, message_id, session_id, timestamp) VALUES (?, ?, ?, ?)",
        (entity_id, message_id, session_id, timestamp),
    )


def get_unprocessed_messages(conn, limit=None):
    """Get messages not yet scanned for entities."""
    sql = """
        SELECT m.id, m.session_id, m.content, m.timestamp, m.role
        FROM messages m
        WHERE m.role = 'user'
        AND m.id NOT IN (SELECT DISTINCT message_id FROM entity_mentions)
        ORDER BY m.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def extract_all(limit=None):
    conn = get_conn(str(DB_PATH))
    rows = get_unprocessed_messages(conn, limit)

    if not rows:
        print("All messages already processed for entities.")
        conn.close()
        return

    print(f"Extracting entities from {len(rows)} messages...")
    total_entities = 0

    for i, (msg_id, session_id, content, timestamp, role) in enumerate(rows):
        entities = extract_entities_from_text(content, msg_id, session_id, timestamp)

        for ent in entities:
            entity_id = upsert_entity(conn, ent["name"], ent["entity_type"], ent["timestamp"])
            store_mention(conn, entity_id, msg_id, session_id, timestamp)
            total_entities += 1

        if not entities:
            # Mark as processed with a self-referencing sentinel
            store_mention(conn, 0, msg_id, session_id, timestamp)

        if (i + 1) % 500 == 0:
            conn.commit()
            print(f"  {i + 1}/{len(rows)} messages processed, {total_entities} entities found")

    conn.commit()
    conn.close()
    print(f"Done. {total_entities} entity mentions from {len(rows)} messages.")


def main():
    parser = argparse.ArgumentParser(description="Claude Memory Entity Extraction")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Extract entities from unprocessed messages")
    run.add_argument("--limit", type=int, help="Max messages to process")

    show = sub.add_parser("show", help="Show top entities")
    show.add_argument("--type", "-t", choices=["library", "file", "service", "project", "concept"])
    show.add_argument("--limit", "-n", type=int, default=30)
    show.add_argument("--min-mentions", type=int, default=2)

    find = sub.add_parser("find", help="Find sessions mentioning an entity")
    find.add_argument("name", help="Entity name to search for")

    stats = sub.add_parser("stats", help="Show entity statistics")

    args = parser.parse_args()

    if args.command == "run":
        extract_all(limit=args.limit)
    elif args.command == "show":
        conn = get_conn(str(DB_PATH))
        sql = """
            SELECT name, entity_type, mention_count, first_seen, last_seen
            FROM entities
            WHERE mention_count >= ? AND id > 0
        """
        params = [args.min_mentions]
        if args.type:
            sql += " AND entity_type = ?"
            params.append(args.type)
        sql += " ORDER BY mention_count DESC LIMIT ?"
        params.append(args.limit)

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        if not rows:
            print("No entities found.")
            sys.exit(1)

        print(f"{'Entity':<35} {'Type':<10} {'Mentions':>8}  {'First Seen':<12} {'Last Seen':<12}")
        print("-" * 85)
        for name, etype, count, first, last in rows:
            fs = (first or "")[:10]
            ls = (last or "")[:10]
            print(f"{name:<35} {etype:<10} {count:>8}  {fs:<12} {ls:<12}")

    elif args.command == "find":
        conn = get_conn(str(DB_PATH))
        rows = conn.execute("""
            SELECT DISTINCT em.session_id, m.project, m.timestamp, m.content
            FROM entity_mentions em
            JOIN entities e ON e.id = em.entity_id
            JOIN messages m ON m.id = em.message_id
            WHERE e.name = ?
            ORDER BY m.timestamp DESC
            LIMIT 20
        """, (args.name.lower(),)).fetchall()
        conn.close()

        if not rows:
            print(f"No mentions of '{args.name}' found.")
            sys.exit(1)

        for session_id, project, ts, content in rows:
            ts_short = (ts or "unknown")[:10]
            proj = project or "no-project"
            snippet = content[:120] + "..." if len(content) > 120 else content
            print(f"[{ts_short}] {proj} (session: {(session_id or 'unknown')[:8]})")
            print(f"  {snippet}\n")

    elif args.command == "stats":
        conn = get_conn(str(DB_PATH))
        total = conn.execute("SELECT COUNT(*) FROM entities WHERE id > 0").fetchone()[0]
        mentions = conn.execute("SELECT COUNT(*) FROM entity_mentions WHERE entity_id > 0").fetchone()[0]
        types = conn.execute(
            "SELECT entity_type, COUNT(*) FROM entities WHERE id > 0 GROUP BY entity_type ORDER BY COUNT(*) DESC"
        ).fetchall()
        processed = conn.execute("SELECT COUNT(DISTINCT message_id) FROM entity_mentions").fetchone()[0]
        total_msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE role = 'user'").fetchone()[0]
        conn.close()

        print(f"Unique entities: {total}")
        print(f"Total mentions:  {mentions}")
        print(f"Messages scanned: {processed}/{total_msgs}")
        for etype, count in types:
            print(f"  {etype}: {count}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
