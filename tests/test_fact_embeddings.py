"""Tests for fact_embeddings feature — Task 13a + 13b.

Covers: migration creates table, CASCADE delete, store_facts persists embeddings,
_load_existing_fact_embeddings uses persisted data, backfill works, CLI --help.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_with_schema() -> sqlite3.Connection:
    """Create an in-memory DB from schema.sql with row_factory and foreign_keys."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _insert_fact(conn: sqlite3.Connection, fact_text: str = "test fact",
                 category: str = "preference", confidence: float = 0.9,
                 project: str = "/test/proj", session_id: str = "sess-1") -> int:
    """Insert a fact directly and return its id."""
    conn.execute(
        """INSERT INTO facts (session_id, project, fact, category, confidence, timestamp)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (session_id, project, fact_text, category, confidence),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_fact_embedding(conn: sqlite3.Connection, fact_id: int,
                           embedding: np.ndarray, model: str = "all-MiniLM-L6-v2") -> None:
    """Insert a fact embedding directly."""
    conn.execute(
        "INSERT INTO fact_embeddings (fact_id, embedding, model) VALUES (?, ?, ?)",
        (fact_id, embedding.astype(np.float32).tobytes(), model),
    )


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

class TestMigration6CreatesFactEmbeddings:
    """VAL-SCHEMA-001: Migration 6 creates the fact_embeddings table."""

    def test_table_exists_after_schema(self) -> None:
        """fact_embeddings table is created by schema.sql."""
        conn = _make_db_with_schema()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fact_embeddings'"
        ).fetchall()
        assert len(rows) == 1, "fact_embeddings table should exist"
        conn.close()

    def test_table_columns(self) -> None:
        """fact_embeddings has the expected columns: fact_id, embedding, model, created_at."""
        conn = _make_db_with_schema()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_embeddings)").fetchall()}
        assert cols == {"fact_id", "embedding", "model", "created_at"}
        conn.close()

    def test_migration_creates_table(self) -> None:
        """migrate_schema() creates fact_embeddings on a DB that has facts but not fact_embeddings."""
        from src.memory_db import migrate_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create a minimal schema with facts table but NO fact_embeddings
        conn.executescript("""
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                project TEXT,
                fact TEXT NOT NULL,
                category TEXT,
                confidence REAL DEFAULT 1.0,
                source_message_id INTEGER,
                timestamp TEXT,
                last_validated TEXT,
                compressed_details TEXT
            );
            CREATE TABLE entity_mentions (entity_id INTEGER, message_id INTEGER);
        """)
        # Confirm fact_embeddings doesn't exist yet
        tables_before = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "fact_embeddings" not in tables_before

        migrate_schema(conn)

        tables_after = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "fact_embeddings" in tables_after
        conn.close()


class TestFactEmbeddingsCascadeDelete:
    """VAL-SCHEMA-003: ON DELETE CASCADE from facts to fact_embeddings."""

    def test_cascade_delete(self) -> None:
        """Deleting a fact cascades to delete its embedding row."""
        conn = _make_db_with_schema()
        fact_id = _insert_fact(conn, "test cascade fact")
        _insert_fact_embedding(conn, fact_id, np.random.randn(384).astype(np.float32))
        conn.commit()

        # Verify embedding exists
        count = conn.execute(
            "SELECT COUNT(*) FROM fact_embeddings WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        assert count == 1

        # Delete the fact
        conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        conn.commit()

        # Verify embedding is gone via CASCADE
        count = conn.execute(
            "SELECT COUNT(*) FROM fact_embeddings WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        assert count == 0
        conn.close()


# ---------------------------------------------------------------------------
# store_facts persists embeddings
# ---------------------------------------------------------------------------

class TestStoreFactsPeristsEmbeddings:
    """VAL-PERSIST-001: store_facts persists embeddings alongside new facts."""

    @patch("src.distill._get_dedup_model")
    @patch("src.distill._load_existing_fact_embeddings")
    def test_embedding_persisted_after_insert(self, mock_load, mock_model) -> None:
        """After store_facts inserts a fact, fact_embeddings has a row for it."""
        conn = _make_db_with_schema()

        # Mock: no existing facts for dedup
        mock_load.return_value = ([], np.array([]))

        # Mock model.encode to return a known vector
        fake_vec = np.random.randn(384).astype(np.float32)
        fake_vec = fake_vec / np.linalg.norm(fake_vec)  # normalize
        mock_model_instance = MagicMock()
        mock_model_instance.encode.return_value = fake_vec.reshape(1, -1)
        mock_model.return_value = mock_model_instance

        from src.distill import store_facts

        facts = [{
            "fact": "User prefers dark mode",
            "category": "preference",
            "confidence": 0.9,
            "session_id": "sess-1",
            "project": "/test",
            "source_message_id": None,
            "timestamp": "2024-01-01T00:00:00",
            "compressed_details": "",
        }]

        inserted = store_facts(conn, facts)
        assert inserted == 1

        # Check fact_embeddings table
        rows = conn.execute("SELECT fact_id, model FROM fact_embeddings").fetchall()
        assert len(rows) == 1
        assert rows[0]["model"] == "all-MiniLM-L6-v2"

        # Verify the stored embedding matches
        emb_blob = conn.execute(
            "SELECT embedding FROM fact_embeddings WHERE fact_id = ?", (rows[0]["fact_id"],)
        ).fetchone()["embedding"]
        stored_vec = np.frombuffer(emb_blob, dtype=np.float32)
        assert stored_vec.shape[0] == 384
        conn.close()


# ---------------------------------------------------------------------------
# _load_existing_fact_embeddings uses persisted data
# ---------------------------------------------------------------------------

class TestLoadExistingFactEmbeddings:
    """VAL-PERSIST-002: _load_existing_fact_embeddings uses persisted embeddings."""

    @patch("src.distill._get_dedup_model")
    def test_loads_persisted_embeddings(self, mock_model) -> None:
        """Facts with persisted embeddings are loaded from DB, not re-encoded."""
        conn = _make_db_with_schema()

        # Insert two facts: one with persisted embedding, one without
        fid1 = _insert_fact(conn, "fact with embedding", confidence=0.9)
        fid2 = _insert_fact(conn, "fact without embedding", confidence=0.8)

        known_vec = np.ones(384, dtype=np.float32)
        known_vec = known_vec / np.linalg.norm(known_vec)
        _insert_fact_embedding(conn, fid1, known_vec)
        conn.commit()

        # Mock model for the fallback encoding
        fallback_vec = np.zeros(384, dtype=np.float32)
        fallback_vec[0] = 1.0  # different from known_vec
        mock_model_instance = MagicMock()
        mock_model_instance.encode.return_value = np.array([fallback_vec])
        mock_model.return_value = mock_model_instance

        from src.distill import _load_existing_fact_embeddings

        facts_list, emb_matrix = _load_existing_fact_embeddings(conn)

        assert len(facts_list) == 2
        assert emb_matrix.shape == (2, 384)

        # The first fact should have the known persisted vector
        persisted_idx = next(i for i, f in enumerate(facts_list) if f["id"] == fid1)
        np.testing.assert_allclose(emb_matrix[persisted_idx], known_vec, atol=1e-6)

        # The second fact should have been encoded by the model
        fallback_idx = next(i for i, f in enumerate(facts_list) if f["id"] == fid2)
        np.testing.assert_allclose(emb_matrix[fallback_idx], fallback_vec, atol=1e-6)
        conn.close()

    def test_empty_facts(self) -> None:
        """Returns empty arrays when there are no facts."""
        conn = _make_db_with_schema()

        from src.distill import _load_existing_fact_embeddings

        facts_list, emb_matrix = _load_existing_fact_embeddings(conn)
        assert facts_list == []
        assert emb_matrix.size == 0
        conn.close()

    @patch("src.distill._get_dedup_model")
    def test_all_persisted_no_encoding(self, mock_model) -> None:
        """When all facts have persisted embeddings, model.encode is not called."""
        conn = _make_db_with_schema()

        fid = _insert_fact(conn, "fully persisted fact", confidence=0.9)
        vec = np.random.randn(384).astype(np.float32)
        _insert_fact_embedding(conn, fid, vec)
        conn.commit()

        mock_model_instance = MagicMock()
        mock_model.return_value = mock_model_instance

        from src.distill import _load_existing_fact_embeddings

        facts_list, emb_matrix = _load_existing_fact_embeddings(conn)
        assert len(facts_list) == 1
        assert emb_matrix.shape[0] == 1

        # Model.encode should NOT have been called
        mock_model_instance.encode.assert_not_called()
        conn.close()


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

class TestBackfillFactEmbeddings:
    """VAL-PERSIST-004: backfill generates embeddings for facts missing them."""

    @patch("src.distill._get_dedup_model")
    def test_backfill_missing(self, mock_model) -> None:
        """Backfill creates embeddings for facts without them, skips those with."""
        conn = _make_db_with_schema()

        # Insert facts: one with embedding, one without
        fid1 = _insert_fact(conn, "fact already has embedding", confidence=0.9)
        fid2 = _insert_fact(conn, "fact needs backfill", confidence=0.8)

        existing_vec = np.random.randn(384).astype(np.float32)
        _insert_fact_embedding(conn, fid1, existing_vec)
        conn.commit()

        # Mock model for backfill encoding
        backfill_vec = np.random.randn(384).astype(np.float32)
        mock_model_instance = MagicMock()
        mock_model_instance.encode.return_value = np.array([backfill_vec])
        mock_model.return_value = mock_model_instance

        from src.distill import backfill_fact_embeddings

        # Wrap conn in a proxy that suppresses close() so we can inspect after
        class _NoCloseConn:
            def __init__(self, real):
                self._real = real
            def close(self):
                pass  # suppress
            def __getattr__(self, name):
                return getattr(self._real, name)

        with patch("src.distill.get_conn", return_value=_NoCloseConn(conn)):
            count = backfill_fact_embeddings()

        assert count == 1  # Only the one missing embedding

        # Verify both facts now have embeddings
        all_emb = conn.execute("SELECT fact_id FROM fact_embeddings ORDER BY fact_id").fetchall()
        assert len(all_emb) == 2
        assert {r["fact_id"] for r in all_emb} == {fid1, fid2}
        conn.close()

    def test_backfill_all_present(self) -> None:
        """Backfill returns 0 when all facts already have embeddings."""
        conn = _make_db_with_schema()

        fid = _insert_fact(conn, "complete fact", confidence=0.9)
        _insert_fact_embedding(conn, fid, np.random.randn(384).astype(np.float32))
        conn.commit()

        from src.distill import backfill_fact_embeddings

        with patch("src.distill.get_conn", return_value=conn):
            count = backfill_fact_embeddings()

        assert count == 0
        conn.close()


# ---------------------------------------------------------------------------
# CLI --help
# ---------------------------------------------------------------------------

class TestBackfillCLI:
    """VAL-PERSIST-003: backfill_embeddings CLI command is registered."""

    def test_help_exits_0(self) -> None:
        """python3 src/distill.py backfill_embeddings --help exits with code 0."""
        result = subprocess.run(
            [sys.executable, "src/distill.py", "backfill_embeddings", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "backfill_embeddings" in result.stdout or "embeddings" in result.stdout
