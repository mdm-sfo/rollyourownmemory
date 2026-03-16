#!/usr/bin/env python3
"""Shared database query module — all SQLite access goes through here.

Provides connection management with WAL mode and busy_timeout, plus shared
query functions used across mcp_server, claude_recall, inject, distill, etc.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

    # Migration 2: Add compressed_details column to facts table
    if "compressed_details" not in columns:
        conn.execute("ALTER TABLE facts ADD COLUMN compressed_details TEXT")

    # Migration 3: Drop and recreate facts_fts with compressed_details column
    # FTS5 virtual tables cannot be ALTERed — must drop and recreate
    # Check if facts_fts already includes compressed_details
    needs_fts_rebuild = False
    try:
        # If facts_fts doesn't have compressed_details, this will fail
        conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('integrity-check')")
        # Check column count by attempting a match that uses compressed_details
        test_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='facts_fts'"
        ).fetchone()
        if test_row and "compressed_details" not in (test_row[0] or ""):
            needs_fts_rebuild = True
    except sqlite3.OperationalError:
        needs_fts_rebuild = True

    if needs_fts_rebuild:
        # Drop old triggers first (they reference the old FTS schema)
        conn.execute("DROP TRIGGER IF EXISTS facts_ai")
        conn.execute("DROP TRIGGER IF EXISTS facts_au")
        conn.execute("DROP TRIGGER IF EXISTS facts_ad")

        # Drop and recreate facts_fts with compressed_details
        conn.execute("DROP TABLE IF EXISTS facts_fts")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                fact,
                category,
                compressed_details,
                content='facts',
                content_rowid='id',
                tokenize='porter unicode61'
            )
        """)

        # Recreate triggers with compressed_details
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, fact, category, compressed_details)
                VALUES (new.id, new.fact, new.category, new.compressed_details);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, fact, category, compressed_details)
                VALUES ('delete', old.id, old.fact, old.category, old.compressed_details);
                INSERT INTO facts_fts(rowid, fact, category, compressed_details)
                VALUES (new.id, new.fact, new.category, new.compressed_details);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, fact, category, compressed_details)
                VALUES ('delete', old.id, old.fact, old.category, old.compressed_details);
            END
        """)

        # Rebuild FTS index from existing data
        conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")

    # Migration 4: Create processed_messages table
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

    # Migration 5: Clean up entity_id=0 sentinels from entity_mentions (conditional)
    sentinel_count = conn.execute(
        "SELECT COUNT(*) FROM entity_mentions WHERE entity_id = 0"
    ).fetchone()[0]
    if sentinel_count > 0:
        conn.execute("DELETE FROM entity_mentions WHERE entity_id = 0")

    # Migration 6: Create fact_embeddings table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fact_embeddings (
            fact_id INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

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
                     project: Optional[str] = None,
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

    if project:
        sql += " AND f.project LIKE ?"
        params.append(f"%{project}%")

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


def search_facts_semantic(conn: sqlite3.Connection, query_vec: Any,
                          category: Optional[str] = None,
                          project: Optional[str] = None,
                          limit: int = 10) -> list[dict]:
    """Semantic search over fact embeddings using cosine similarity.

    Args:
        conn: SQLite connection.
        query_vec: numpy array — the query embedding (already normalized).
        category: Optional category filter.
        project: Optional project filter.
        limit: Max results to return.

    Returns:
        List of fact dicts with an added 'score' key, sorted by descending similarity.
    """
    import numpy as np

    sql = """
        SELECT f.id, f.fact, f.category, f.confidence, f.project,
               f.timestamp, f.compressed_details, fe.embedding
        FROM fact_embeddings fe
        JOIN facts f ON f.id = fe.fact_id
        WHERE f.confidence > 0
    """
    params: list = []

    if category:
        sql += " AND f.category = ?"
        params.append(category)
    if project:
        sql += " AND f.project LIKE ?"
        params.append(f"%{project}%")

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []

    # Filter to only embeddings that match the query vector's dimension
    facts = []
    embeddings = []
    query_dim = query_vec.shape[0]
    for r in rows:
        vec = np.frombuffer(r["embedding"], dtype=np.float32)
        if vec.shape[0] == query_dim:
            facts.append(dict(r))
            embeddings.append(vec)

    if not embeddings:
        return []

    embeddings_matrix = np.stack(embeddings)
    query_vec = query_vec.astype(np.float32)
    similarities = embeddings_matrix @ query_vec

    top_indices = np.argsort(similarities)[::-1][:limit]

    results = []
    for idx in top_indices:
        fact = facts[idx]
        fact.pop("embedding", None)
        fact["score"] = float(similarities[idx])
        if fact["score"] > 0.3:  # Only return minimally relevant results
            results.append(fact)

    return results


def store_fact(conn: sqlite3.Connection, fact: str, category: str,
               confidence: float, project: Optional[str] = None,
               session_id: Optional[str] = None,
               source_message_id: Optional[int] = None,
               timestamp: Optional[str] = None,
               last_validated: Optional[str] = None,
               compressed_details: Optional[str] = None) -> int:
    """Insert a fact and return its row id.

    Accepts optional last_validated for fact decay tracking and optional
    compressed_details for noting omitted specifics during extraction.
    """
    sql = """INSERT INTO facts
             (fact, category, confidence, project, session_id,
              source_message_id, timestamp, last_validated, compressed_details)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    cur = conn.execute(sql, (fact, category, confidence, project,
                             session_id, source_message_id, timestamp,
                             last_validated, compressed_details))
    conn.commit()
    return cur.lastrowid
