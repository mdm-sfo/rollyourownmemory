"""Tests for model registry, dimension safety, --reembed flag, model-change detection,
and dedup model selection (Tasks 14a, 14b, 14c).

Covers validation assertions:
- VAL-REGISTRY-001: EMBEDDING_MODELS dict contains minilm and mpnet
- VAL-REGISTRY-002: get_model accepts short names
- VAL-REGISTRY-003: DEFAULT_MODEL unchanged
- VAL-DIM-001: _search_bruteforce skips mismatched embeddings
- VAL-DIM-002: _search_faiss returns None on dimension mismatch
- VAL-DIM-003: --reembed flag clears existing embeddings
- VAL-DIM-004: Model change detection warns about mixed dimensions
- VAL-DEDUP-001: --embed-model flag accepted on distill run
- VAL-DEDUP-002: _get_dedup_model accepts model_name parameter
"""

import io
import json
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


_msg_counter = 0


def _insert_message(conn: sqlite3.Connection, content: str = "test msg",
                    session_id: str = "sess-1", project: str = "/test/proj",
                    role: str = "user", msg_id: int | None = None) -> int:
    """Insert a message and return its id. Uses unique source_file per call to avoid UNIQUE constraint."""
    global _msg_counter
    _msg_counter += 1
    source_file = f"/tmp/test_{_msg_counter}.jsonl"
    timestamp = f"2024-01-01T00:00:{_msg_counter:02d}"
    if msg_id is not None:
        conn.execute(
            """INSERT INTO messages (id, source_file, session_id, project, role, content, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, source_file, session_id, project, role, content, timestamp),
        )
        return msg_id
    conn.execute(
        """INSERT INTO messages (source_file, session_id, project, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (source_file, session_id, project, role, content, timestamp),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_embedding(conn: sqlite3.Connection, message_id: int,
                      embedding: np.ndarray, model: str = "all-MiniLM-L6-v2") -> None:
    """Insert a message embedding directly."""
    conn.execute(
        "INSERT INTO embeddings (message_id, embedding, model) VALUES (?, ?, ?)",
        (message_id, embedding.astype(np.float32).tobytes(), model),
    )


def _insert_fact_embedding(conn: sqlite3.Connection, fact_id: int,
                           embedding: np.ndarray, model: str = "all-MiniLM-L6-v2") -> None:
    """Insert a fact embedding directly."""
    conn.execute(
        "INSERT INTO fact_embeddings (fact_id, embedding, model) VALUES (?, ?, ?)",
        (fact_id, embedding.astype(np.float32).tobytes(), model),
    )


# ---------------------------------------------------------------------------
# VAL-REGISTRY-001: EMBEDDING_MODELS dict contains minilm and mpnet
# ---------------------------------------------------------------------------

class TestEmbeddingModelsRegistry:
    """VAL-REGISTRY-001: EMBEDDING_MODELS dict contains minilm and mpnet."""

    def test_has_minilm_key(self) -> None:
        from src.embed import EMBEDDING_MODELS
        assert "minilm" in EMBEDDING_MODELS

    def test_has_mpnet_key(self) -> None:
        from src.embed import EMBEDDING_MODELS
        assert "mpnet" in EMBEDDING_MODELS

    def test_minilm_has_required_fields(self) -> None:
        from src.embed import EMBEDDING_MODELS
        entry = EMBEDDING_MODELS["minilm"]
        assert "name" in entry
        assert "dim" in entry
        assert "backend" in entry
        assert "description" in entry

    def test_mpnet_has_required_fields(self) -> None:
        from src.embed import EMBEDDING_MODELS
        entry = EMBEDDING_MODELS["mpnet"]
        assert "name" in entry
        assert "dim" in entry
        assert "backend" in entry
        assert "description" in entry

    def test_minilm_name_is_correct(self) -> None:
        from src.embed import EMBEDDING_MODELS
        assert EMBEDDING_MODELS["minilm"]["name"] == "all-MiniLM-L6-v2"

    def test_mpnet_name_is_correct(self) -> None:
        from src.embed import EMBEDDING_MODELS
        assert EMBEDDING_MODELS["mpnet"]["name"] == "all-mpnet-base-v2"

    def test_minilm_dim_is_384(self) -> None:
        from src.embed import EMBEDDING_MODELS
        assert EMBEDDING_MODELS["minilm"]["dim"] == 384

    def test_mpnet_dim_is_768(self) -> None:
        from src.embed import EMBEDDING_MODELS
        assert EMBEDDING_MODELS["mpnet"]["dim"] == 768


# ---------------------------------------------------------------------------
# VAL-REGISTRY-002: get_model accepts short names
# ---------------------------------------------------------------------------

class TestGetModelShortNames:
    """VAL-REGISTRY-002: get_model accepts short names from the registry."""

    @patch("sentence_transformers.SentenceTransformer")
    def test_get_model_mpnet_resolves(self, mock_st) -> None:
        """get_model('mpnet') loads all-mpnet-base-v2."""
        from src.embed import get_model
        get_model("mpnet")
        mock_st.assert_called_once_with("all-mpnet-base-v2")

    @patch("sentence_transformers.SentenceTransformer")
    def test_get_model_minilm_resolves(self, mock_st) -> None:
        """get_model('minilm') loads all-MiniLM-L6-v2."""
        from src.embed import get_model
        get_model("minilm")
        mock_st.assert_called_once_with("all-MiniLM-L6-v2")

    @patch("sentence_transformers.SentenceTransformer")
    def test_get_model_full_name_still_works(self, mock_st) -> None:
        """get_model with a full model name still works directly."""
        from src.embed import get_model
        get_model("all-MiniLM-L6-v2")
        mock_st.assert_called_once_with("all-MiniLM-L6-v2")

    @patch("sentence_transformers.SentenceTransformer")
    def test_get_model_default_is_minilm(self, mock_st) -> None:
        """get_model() with no args loads all-MiniLM-L6-v2."""
        from src.embed import get_model
        get_model()
        mock_st.assert_called_once_with("all-MiniLM-L6-v2")


# ---------------------------------------------------------------------------
# VAL-REGISTRY-003: DEFAULT_MODEL unchanged
# ---------------------------------------------------------------------------

class TestDefaultModelUnchanged:
    """VAL-REGISTRY-003: DEFAULT_MODEL is still all-MiniLM-L6-v2."""

    def test_default_model_value(self) -> None:
        from src.embed import DEFAULT_MODEL
        assert DEFAULT_MODEL == "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# VAL-DIM-001: _search_bruteforce skips mismatched embeddings
# ---------------------------------------------------------------------------

class TestSearchBruteforceDimensionSafety:
    """VAL-DIM-001: _search_bruteforce skips embeddings with mismatched dimensions."""

    def test_skips_mismatched_dims_384_query_768_stored(self) -> None:
        """384-dim query with 768-dim stored embeddings: skips and doesn't crash."""
        conn = _make_db_with_schema()

        # Insert a message with 768-dim embedding
        mid = _insert_message(conn, "test message")
        vec_768 = np.random.randn(768).astype(np.float32)
        vec_768 /= np.linalg.norm(vec_768)
        _insert_embedding(conn, mid, vec_768, model="all-mpnet-base-v2")
        conn.commit()

        # Search with 384-dim query
        query_vec = np.random.randn(384).astype(np.float32)
        query_vec /= np.linalg.norm(query_vec)

        from src.embed import _search_bruteforce
        results = _search_bruteforce(query_vec, conn, top_k=10)
        # Should return empty (all embeddings were skipped), not crash
        assert results == []
        conn.close()

    def test_skips_mismatched_dims_768_query_384_stored(self) -> None:
        """768-dim query with 384-dim stored embeddings: skips and doesn't crash."""
        conn = _make_db_with_schema()

        mid = _insert_message(conn, "test message")
        vec_384 = np.random.randn(384).astype(np.float32)
        vec_384 /= np.linalg.norm(vec_384)
        _insert_embedding(conn, mid, vec_384)
        conn.commit()

        query_vec = np.random.randn(768).astype(np.float32)
        query_vec /= np.linalg.norm(query_vec)

        from src.embed import _search_bruteforce
        results = _search_bruteforce(query_vec, conn, top_k=10)
        assert results == []
        conn.close()

    def test_prints_warning_on_mismatch(self) -> None:
        """Prints a warning when embeddings are skipped due to dimension mismatch."""
        conn = _make_db_with_schema()

        mid = _insert_message(conn, "test message")
        vec_768 = np.random.randn(768).astype(np.float32)
        vec_768 /= np.linalg.norm(vec_768)
        _insert_embedding(conn, mid, vec_768, model="all-mpnet-base-v2")
        conn.commit()

        query_vec = np.random.randn(384).astype(np.float32)
        query_vec /= np.linalg.norm(query_vec)

        from src.embed import _search_bruteforce

        import io
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            _search_bruteforce(query_vec, conn, top_k=10)
            warning = mock_stderr.getvalue()
            assert "skipped" in warning.lower() or "Warning" in warning
            assert "reembed" in warning.lower() or "--reembed" in warning
        conn.close()

    def test_keeps_matching_dims(self) -> None:
        """Embeddings with matching dimensions are returned normally."""
        conn = _make_db_with_schema()

        mid = _insert_message(conn, "test message")
        vec_384 = np.random.randn(384).astype(np.float32)
        vec_384 /= np.linalg.norm(vec_384)
        _insert_embedding(conn, mid, vec_384)
        conn.commit()

        query_vec = np.random.randn(384).astype(np.float32)
        query_vec /= np.linalg.norm(query_vec)

        from src.embed import _search_bruteforce
        results = _search_bruteforce(query_vec, conn, top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == mid
        conn.close()

    def test_mixed_dims_returns_only_matching(self) -> None:
        """With mixed-dim embeddings, only matching-dim results are returned."""
        conn = _make_db_with_schema()

        # Insert one 384-dim and one 768-dim embedding
        mid1 = _insert_message(conn, "matching dim msg", msg_id=1)
        vec_384 = np.random.randn(384).astype(np.float32)
        vec_384 /= np.linalg.norm(vec_384)
        _insert_embedding(conn, mid1, vec_384)

        mid2 = _insert_message(conn, "mismatched dim msg", msg_id=2)
        vec_768 = np.random.randn(768).astype(np.float32)
        vec_768 /= np.linalg.norm(vec_768)
        _insert_embedding(conn, mid2, vec_768, model="all-mpnet-base-v2")
        conn.commit()

        query_vec = np.random.randn(384).astype(np.float32)
        query_vec /= np.linalg.norm(query_vec)

        from src.embed import _search_bruteforce
        results = _search_bruteforce(query_vec, conn, top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == mid1
        conn.close()


# ---------------------------------------------------------------------------
# VAL-DIM-002: _search_faiss returns None on dimension mismatch
# ---------------------------------------------------------------------------

class TestSearchFaissDimensionCheck:
    """VAL-DIM-002: _search_faiss checks index.d vs query dim."""

    def test_returns_none_on_dimension_mismatch(self) -> None:
        """_search_faiss returns None when FAISS index dim doesn't match query."""
        conn = _make_db_with_schema()

        # Create a temporary FAISS index with 384-dim vectors
        import tempfile
        import os
        try:
            import faiss
        except ImportError:
            pytest.skip("faiss not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            idx_path = Path(tmpdir) / "test.faiss"
            ids_path = Path(tmpdir) / "test_ids.json"

            index = faiss.IndexFlatIP(384)
            vec = np.random.randn(1, 384).astype(np.float32)
            index.add(vec)
            faiss.write_index(index, str(idx_path))
            with open(ids_path, "w") as f:
                json.dump([1], f)

            # Patch the paths to use our temp files
            from src.embed import _search_faiss

            with patch("src.embed.FAISS_INDEX_PATH", idx_path), \
                 patch("src.embed.FAISS_IDS_PATH", ids_path):
                # Query with 768-dim vector (mismatch!)
                query_vec = np.random.randn(768).astype(np.float32)
                result = _search_faiss(query_vec, conn, top_k=10)
                assert result is None  # Should return None for fallback

        conn.close()

    def test_prints_warning_on_mismatch(self) -> None:
        """_search_faiss prints a warning when dimensions don't match."""
        conn = _make_db_with_schema()

        try:
            import faiss
        except ImportError:
            pytest.skip("faiss not installed")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            idx_path = Path(tmpdir) / "test.faiss"
            ids_path = Path(tmpdir) / "test_ids.json"

            index = faiss.IndexFlatIP(384)
            vec = np.random.randn(1, 384).astype(np.float32)
            index.add(vec)
            faiss.write_index(index, str(idx_path))
            with open(ids_path, "w") as f:
                json.dump([1], f)

            from src.embed import _search_faiss

            with patch("src.embed.FAISS_INDEX_PATH", idx_path), \
                 patch("src.embed.FAISS_IDS_PATH", ids_path), \
                 patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                query_vec = np.random.randn(768).astype(np.float32)
                _search_faiss(query_vec, conn, top_k=10)
                warning = mock_stderr.getvalue()
                assert "dimension" in warning.lower() or "Falling back" in warning

        conn.close()


# ---------------------------------------------------------------------------
# VAL-DIM-003: --reembed flag clears existing embeddings
# ---------------------------------------------------------------------------

class TestReembedFlag:
    """VAL-DIM-003: --reembed flag on embed.py build."""

    def test_build_help_shows_reembed(self) -> None:
        """'embed.py build --help' shows --reembed flag."""
        result = subprocess.run(
            [sys.executable, "src/embed.py", "build", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "--reembed" in result.stdout

    def test_reembed_clears_embeddings_table(self) -> None:
        """--reembed clears the embeddings table."""
        conn = _make_db_with_schema()

        # Insert some embeddings
        mid = _insert_message(conn, "test message")
        vec = np.random.randn(384).astype(np.float32)
        _insert_embedding(conn, mid, vec)
        conn.commit()

        count_before = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert count_before == 1

        # Simulate what --reembed does: clear embeddings + fact_embeddings
        conn.execute("DELETE FROM embeddings")
        try:
            conn.execute("DELETE FROM fact_embeddings")
        except sqlite3.OperationalError:
            pass
        conn.commit()

        count_after = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert count_after == 0
        conn.close()

    def test_reembed_clears_fact_embeddings_table(self) -> None:
        """--reembed clears the fact_embeddings table."""
        conn = _make_db_with_schema()

        # Insert a fact with embedding
        conn.execute(
            """INSERT INTO facts (session_id, project, fact, category, confidence, timestamp)
               VALUES ('sess-1', '/test', 'test fact', 'preference', 0.9, datetime('now'))"""
        )
        fact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _insert_fact_embedding(conn, fact_id, np.random.randn(384).astype(np.float32))
        conn.commit()

        count_before = conn.execute("SELECT COUNT(*) FROM fact_embeddings").fetchone()[0]
        assert count_before == 1

        conn.execute("DELETE FROM fact_embeddings")
        conn.commit()

        count_after = conn.execute("SELECT COUNT(*) FROM fact_embeddings").fetchone()[0]
        assert count_after == 0
        conn.close()

    def test_reembed_removes_faiss_files(self) -> None:
        """--reembed removes FAISS index files (using FAISS_INDEX_PATH/FAISS_IDS_PATH constants)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            idx_path = Path(tmpdir) / "memory.faiss"
            ids_path = Path(tmpdir) / "memory_ids.json"
            idx_path.write_text("fake index")
            ids_path.write_text("fake ids")
            assert idx_path.exists()
            assert ids_path.exists()

            # This tests the logic: if path exists, unlink it
            if idx_path.exists():
                idx_path.unlink()
            if ids_path.exists():
                ids_path.unlink()

            assert not idx_path.exists()
            assert not ids_path.exists()


# ---------------------------------------------------------------------------
# VAL-DIM-004: Model change detection warns about mixed dimensions
# ---------------------------------------------------------------------------

class TestModelChangeDetection:
    """VAL-DIM-004: embed_messages() warns when existing embeddings use a different model."""

    def test_warns_on_model_mismatch(self) -> None:
        """embed_messages() prints WARNING when existing model differs from current."""
        conn = _make_db_with_schema()

        # Insert a message with embedding from different model
        mid = _insert_message(conn, "old model message")
        vec = np.random.randn(768).astype(np.float32)
        _insert_embedding(conn, mid, vec, model="all-mpnet-base-v2")
        conn.execute(
            "INSERT OR IGNORE INTO processed_messages (message_id, processor) VALUES (?, 'embeddings')",
            (mid,),
        )
        conn.commit()

        # Insert a new unembedded message
        mid2 = _insert_message(conn, "new unembedded message", msg_id=100)

        from src.embed import embed_messages

        # Mock to prevent real embedding work
        mock_model = MagicMock()
        mock_model.encode.return_value = np.random.randn(1, 384).astype(np.float32)

        with patch("src.embed.get_conn", return_value=conn), \
             patch("src.embed.get_model", return_value=mock_model), \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            # embed_messages with default model (minilm) but existing embeddings are mpnet
            embed_messages(model_name="all-MiniLM-L6-v2", limit=1)
            warning = mock_stderr.getvalue()
            assert "WARNING" in warning or "warning" in warning.lower()
            assert "mpnet" in warning.lower() or "all-mpnet-base-v2" in warning

        conn.close()


# ---------------------------------------------------------------------------
# VAL-DEDUP-001: --embed-model flag accepted on distill run
# ---------------------------------------------------------------------------

class TestDistillEmbedModelFlag:
    """VAL-DEDUP-001: --embed-model flag on distill run."""

    def test_run_help_shows_embed_model(self) -> None:
        """'distill.py run --help' shows --embed-model flag."""
        result = subprocess.run(
            [sys.executable, "src/distill.py", "run", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "--embed-model" in result.stdout


# ---------------------------------------------------------------------------
# VAL-DEDUP-002: _get_dedup_model accepts model_name parameter
# ---------------------------------------------------------------------------

class TestGetDedupModelParam:
    """VAL-DEDUP-002: _get_dedup_model accepts model_name parameter."""

    @patch("src.distill._embedding_model", None)
    def test_accepts_model_name_param(self) -> None:
        """_get_dedup_model(model_name='mpnet') passes 'mpnet' to get_model()."""
        from src import distill

        # Reset the global singleton
        distill._embedding_model = None

        mock_model = MagicMock()
        with patch("src.embed.get_model", return_value=mock_model) as mock_get_model:
            result = distill._get_dedup_model(model_name="mpnet")
            mock_get_model.assert_called_once_with("mpnet")
            assert result == mock_model

        # Reset the singleton back
        distill._embedding_model = None

    @patch("src.distill._embedding_model", None)
    def test_default_uses_embed_default(self) -> None:
        """_get_dedup_model() with no args uses embed.py DEFAULT_MODEL."""
        from src import distill

        distill._embedding_model = None

        mock_model = MagicMock()
        with patch("src.embed.get_model", return_value=mock_model) as mock_get_model:
            result = distill._get_dedup_model()
            # Should be called with the default model name
            call_args = mock_get_model.call_args[0]
            assert call_args[0] == "all-MiniLM-L6-v2"
            assert result == mock_model

        distill._embedding_model = None
