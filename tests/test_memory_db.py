"""Tests for src/memory_db.py — get_conn pragmas, FTS search, fact round-trip, semantic fact search."""

import sqlite3
from pathlib import Path

import numpy as np

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


# ---------------------------------------------------------------------------
# Helpers for semantic fact search tests
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
# search_facts_semantic tests — VAL-SEMFACT-001 through VAL-SEMFACT-004
# ---------------------------------------------------------------------------

class TestSearchFactsSemantic:
    """search_facts_semantic() brute-force cosine similarity over fact_embeddings."""

    def test_returns_ranked_results(self) -> None:
        """VAL-SEMFACT-001: Results sorted by descending cosine similarity with 'score' key."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        # Create 3 facts with known embeddings
        fid1 = _insert_fact(conn, "fact alpha")
        fid2 = _insert_fact(conn, "fact beta")
        fid3 = _insert_fact(conn, "fact gamma")

        # Create embeddings where similarity to query varies
        # query_vec: [1, 0, 0, ...] (384-dim)
        query_vec = np.zeros(384, dtype=np.float32)
        query_vec[0] = 1.0

        # vec1: very similar (0.99 similarity)
        vec1 = np.zeros(384, dtype=np.float32)
        vec1[0] = 0.99
        vec1[1] = 0.14  # small perpendicular component
        vec1 = vec1 / np.linalg.norm(vec1)

        # vec2: somewhat similar (0.7 similarity)
        vec2 = np.zeros(384, dtype=np.float32)
        vec2[0] = 0.7
        vec2[1] = 0.71
        vec2 = vec2 / np.linalg.norm(vec2)

        # vec3: moderately similar (0.5 similarity)
        vec3 = np.zeros(384, dtype=np.float32)
        vec3[0] = 0.5
        vec3[1] = 0.87
        vec3 = vec3 / np.linalg.norm(vec3)

        _insert_fact_embedding(conn, fid1, vec1)
        _insert_fact_embedding(conn, fid2, vec2)
        _insert_fact_embedding(conn, fid3, vec3)
        conn.commit()

        results = search_facts_semantic(conn, query_vec)

        assert len(results) == 3
        # Check sorted by descending score
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)
        # All results should have 'score' key
        for r in results:
            assert "score" in r
            assert isinstance(r["score"], float)
        conn.close()

    def test_filters_by_dimension(self) -> None:
        """VAL-SEMFACT-002: Skips embeddings with different dimensions."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        fid1 = _insert_fact(conn, "fact 384 dim")
        fid2 = _insert_fact(conn, "fact 768 dim")

        # 384-dim embedding
        vec384 = np.random.randn(384).astype(np.float32)
        vec384 = vec384 / np.linalg.norm(vec384)

        # 768-dim embedding (different model)
        vec768 = np.random.randn(768).astype(np.float32)
        vec768 = vec768 / np.linalg.norm(vec768)

        _insert_fact_embedding(conn, fid1, vec384)
        _insert_fact_embedding(conn, fid2, vec768)
        conn.commit()

        # Query with 384-dim vector — should only get the 384-dim fact
        query_vec = np.random.randn(384).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        results = search_facts_semantic(conn, query_vec)

        # Should not crash, and should only include the matching-dimension fact
        fact_ids = [r["id"] for r in results]
        assert fid2 not in fact_ids  # 768-dim fact should be excluded
        conn.close()

    def test_filters_by_category(self) -> None:
        """VAL-SEMFACT-003: Respects optional category filter."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        fid1 = _insert_fact(conn, "fact preference", category="preference")
        fid2 = _insert_fact(conn, "fact tool", category="tool")

        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        _insert_fact_embedding(conn, fid1, vec.copy())
        _insert_fact_embedding(conn, fid2, vec.copy())
        conn.commit()

        query_vec = vec.copy()
        results = search_facts_semantic(conn, query_vec, category="preference")

        assert all(r["category"] == "preference" for r in results)
        conn.close()

    def test_filters_by_project(self) -> None:
        """VAL-SEMFACT-003: Respects optional project filter."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        fid1 = _insert_fact(conn, "fact proj1", project="/home/user/proj1")
        fid2 = _insert_fact(conn, "fact proj2", project="/home/user/proj2")

        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        _insert_fact_embedding(conn, fid1, vec.copy())
        _insert_fact_embedding(conn, fid2, vec.copy())
        conn.commit()

        query_vec = vec.copy()
        results = search_facts_semantic(conn, query_vec, project="proj1")

        assert all("proj1" in (r.get("project") or "") for r in results)
        conn.close()

    def test_minimum_similarity_threshold(self) -> None:
        """VAL-SEMFACT-004: Results with similarity <= 0.3 are excluded."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        fid_high = _insert_fact(conn, "fact high sim")
        fid_low = _insert_fact(conn, "fact low sim")

        # query vector: [1, 0, 0, ...]
        query_vec = np.zeros(384, dtype=np.float32)
        query_vec[0] = 1.0

        # high similarity embedding: nearly aligned with query
        vec_high = np.zeros(384, dtype=np.float32)
        vec_high[0] = 0.9
        vec_high[1] = 0.4
        vec_high = vec_high / np.linalg.norm(vec_high)

        # low similarity embedding: nearly orthogonal to query
        vec_low = np.zeros(384, dtype=np.float32)
        vec_low[0] = 0.1
        vec_low[1] = 0.99
        vec_low = vec_low / np.linalg.norm(vec_low)

        _insert_fact_embedding(conn, fid_high, vec_high)
        _insert_fact_embedding(conn, fid_low, vec_low)
        conn.commit()

        results = search_facts_semantic(conn, query_vec)

        # All returned results should have score > 0.3
        for r in results:
            assert r["score"] > 0.3
        # The low-sim fact should be excluded
        returned_ids = [r["id"] for r in results]
        assert fid_low not in returned_ids
        conn.close()

    def test_empty_results(self) -> None:
        """Returns empty list when no fact embeddings exist."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        query_vec = np.random.randn(384).astype(np.float32)
        results = search_facts_semantic(conn, query_vec)

        assert results == []
        conn.close()

    def test_limit_parameter(self) -> None:
        """Respects the limit parameter."""
        from src.memory_db import search_facts_semantic

        conn = _make_db_with_schema()

        # Insert 5 facts with high-similarity embeddings
        query_vec = np.random.randn(384).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        for i in range(5):
            fid = _insert_fact(conn, f"fact {i}")
            # Use the query vector itself to guarantee high similarity
            _insert_fact_embedding(conn, fid, query_vec.copy())
        conn.commit()

        results = search_facts_semantic(conn, query_vec, limit=2)

        assert len(results) <= 2
        conn.close()
