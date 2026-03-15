"""Tests for src/memory_db.py — get_conn pragmas, FTS search, fact round-trip."""

import sqlite3
from pathlib import Path

from src.memory_db import get_conn, search_facts_fts, search_fts, store_fact

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


class TestGetConnSetsPragmas:
    """get_conn() must configure WAL mode and busy_timeout."""

    def test_wal_and_busy_timeout(self, tmp_path: Path) -> None:
        """Connection has journal_mode=wal and busy_timeout=5000."""
        db_file = tmp_path / "test.db"
        conn = get_conn(str(db_file))

        # Initialise schema so the DB is usable
        conn.executescript(SCHEMA_PATH.read_text())

        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode == "wal"

        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout == 5000

        conn.close()


class TestSearchFts:
    """search_fts returns matching messages via FTS5."""

    def test_insert_and_search(self, db: sqlite3.Connection) -> None:
        """Insert messages, search via FTS, verify results returned."""
        db.row_factory = sqlite3.Row

        # Insert test messages — the schema trigger keeps FTS in sync
        db.execute(
            "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.jsonl", "sess-1", "/home/user/proj", "user",
             "How do I configure pytest for this project?", "2024-01-01T00:00:00", "local"),
        )
        db.execute(
            "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.jsonl", "sess-1", "/home/user/proj", "assistant",
             "You can add a pytest section to pyproject.toml", "2024-01-01T00:01:00", "local"),
        )
        db.execute(
            "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.jsonl", "sess-2", "/home/user/other", "user",
             "Unrelated message about cooking recipes", "2024-01-02T00:00:00", "local"),
        )
        db.commit()

        results = search_fts(db, "pytest")

        assert len(results) >= 1
        contents = [r["content"] for r in results]
        assert any("pytest" in c for c in contents)


class TestStoreAndSearchFacts:
    """store_fact + search_facts_fts round-trip."""

    def test_round_trip(self, db: sqlite3.Connection) -> None:
        """Store a fact and retrieve it via FTS search."""
        db.row_factory = sqlite3.Row

        fact_id = store_fact(
            db,
            fact="User prefers pytest over unittest for all Python projects",
            category="preference",
            confidence=0.9,
            project="/home/user/myproject",
            session_id="sess-abc",
            source_message_id=None,
            timestamp="2024-06-01T12:00:00",
            last_validated="2024-06-01T12:00:00",
        )
        assert fact_id is not None
        assert fact_id > 0

        results = search_facts_fts(db, "pytest")

        assert len(results) >= 1
        assert any(r["fact"] == "User prefers pytest over unittest for all Python projects"
                   for r in results)

        # Verify fields persisted correctly
        stored = results[0]
        assert stored["category"] == "preference"
        assert stored["confidence"] == 0.9
        assert stored["project"] == "/home/user/myproject"
        assert stored["session_id"] == "sess-abc"
        assert stored["last_validated"] == "2024-06-01T12:00:00"
