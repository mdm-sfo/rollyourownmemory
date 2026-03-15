#!/usr/bin/env python3
"""Shared database query module — all SQLite access goes through here.

Provides connection management with WAL mode and busy_timeout, plus shared
query functions used across mcp_server, claude_recall, inject, distill, etc.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"
SCHEMA_PATH = MEMORY_DIR / "schema.sql"


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply schema migrations. Called from get_conn() on every connection.

    Migrations are idempotent — safe to run multiple times.
    """
    # Migration 1: Add last_validated column to facts table
    columns = {row[1] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
    if "last_validated" not in columns:
        conn.execute("ALTER TABLE facts ADD COLUMN last_validated TEXT")

    # Migration 2: Create processed_messages table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_messages (
            message_id INTEGER NOT NULL,
            processor TEXT NOT NULL,
            processed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (message_id, processor)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_processed_messages_processor
            ON processed_messages(processor)
    """)

    # Migration 3: Clean up entity_id=0 sentinels from entity_mentions
    conn.execute("DELETE FROM entity_mentions WHERE entity_id = 0")

    conn.commit()


def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a SQLite connection with standard pragmas and row_factory.

    Sets journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000,
    and row_factory=sqlite3.Row. Calls migrate_schema() to apply
    any pending migrations.
    """
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")

    # Only run migrations on real databases (not :memory: without tables)
    try:
        conn.execute("SELECT 1 FROM facts LIMIT 0")
        migrate_schema(conn)
    except sqlite3.OperationalError:
        # Table doesn't exist yet (fresh db or :memory:) — skip migrations
        pass

    return conn


def search_fts(conn: sqlite3.Connection, query: str,
               project: Optional[str] = None, since: Optional[str] = None,
               role: Optional[str] = None, limit: int = 5) -> list[dict]:
    """Full-text keyword search via FTS5 on messages."""
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

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_facts_fts(conn: sqlite3.Connection, query: str,
                     category: Optional[str] = None,
                     limit: int = 10) -> list[dict]:
    """Full-text keyword search via FTS5 on facts."""
    sql = """
        SELECT f.* FROM facts_fts
        JOIN facts f ON f.id = facts_fts.rowid
        WHERE facts_fts MATCH ?
        AND f.confidence > 0
    """
    params: list = [query]

    if category:
        sql += " AND f.category = ?"
        params.append(category)

    sql += " ORDER BY f.confidence DESC, f.timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_session_messages(conn: sqlite3.Connection, session_id: str,
                         limit: int = 50) -> list[dict]:
    """Retrieve messages for a session, supporting partial ID prefix matching."""
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
    return [dict(r) for r in rows]


def list_recent_sessions(conn: sqlite3.Connection,
                         project: Optional[str] = None,
                         since: Optional[str] = None,
                         limit: int = 20) -> list[dict]:
    """List recent sessions with summary info."""
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
    return [dict(r) for r in rows]


def store_fact(conn: sqlite3.Connection, fact: str, category: str,
               confidence: float, project: Optional[str] = None,
               session_id: Optional[str] = None,
               source_message_id: Optional[int] = None,
               timestamp: Optional[str] = None,
               last_validated: Optional[str] = None) -> int:
    """Insert a fact and return its row id.

    Accepts an optional last_validated parameter for fact decay tracking.
    """
    sql = """INSERT INTO facts
             (fact, category, confidence, project, session_id,
              source_message_id, timestamp, last_validated)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
    cur = conn.execute(sql, (fact, category, confidence, project,
                             session_id, source_message_id, timestamp,
                             last_validated))
    conn.commit()
    return cur.lastrowid
