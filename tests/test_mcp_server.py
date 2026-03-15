"""Tests for src/mcp_server.py — memory_deep_recall tool."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


@pytest.fixture
def mcp_db(tmp_path: Path):
    """Create a temporary database and patch mcp_server to use it."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.row_factory = sqlite3.Row

    # Insert test messages
    conn.execute(
        "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test.jsonl", "sess-1", "/home/user/proj", "user",
         "How do I configure CORS in Express?", "2024-01-01T00:00:00", "local"),
    )
    conn.execute(
        "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test.jsonl", "sess-1", "/home/user/proj", "assistant",
         "You need to add cors middleware to your Express app", "2024-01-01T00:01:00", "local"),
    )

    # Insert test facts
    conn.execute(
        "INSERT INTO facts (session_id, project, fact, category, confidence, timestamp, compressed_details) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess-1", "/home/user/proj",
         "User configures CORS in Express using cors middleware",
         "tool", 0.9, "2024-01-01T00:02:00", "exact config values, middleware options"),
    )
    conn.commit()
    conn.close()

    # Patch get_conn in mcp_server to return a connection to the test DB
    def patched_get_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    with patch("src.mcp_server.get_conn", side_effect=patched_get_conn):
        yield db_path


class TestMemoryDeepRecall:
    """memory_deep_recall searches facts_fts AND messages_fts."""

    def test_tool_exists_with_correct_signature(self) -> None:
        """memory_deep_recall is importable and has the right parameters."""
        from src.mcp_server import memory_deep_recall
        import inspect

        sig = inspect.signature(memory_deep_recall)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "synthesize" in params
        assert "limit" in params
        assert "project" in params

        # Check defaults
        assert sig.parameters["synthesize"].default is True
        assert sig.parameters["limit"].default == 10
        assert sig.parameters["project"].default is None

    def test_searches_facts_and_messages(self, mcp_db: Path) -> None:
        """Results include both facts and messages matching the query."""
        from src.mcp_server import memory_deep_recall

        result = memory_deep_recall("CORS", synthesize=False)
        assert "Deep Recall" in result
        assert "CORS" in result
        # Should find facts
        assert "Extracted Facts" in result or "Supporting Facts" in result
        # Should find messages
        assert "Source Messages" in result

    def test_empty_results_returns_no_memories(self, mcp_db: Path) -> None:
        """Returns 'No memories found' when no matches exist."""
        from src.mcp_server import memory_deep_recall

        result = memory_deep_recall("xyznonexistent12345", synthesize=False)
        assert "No memories found" in result

    def test_llm_failure_falls_back_to_raw(self, mcp_db: Path) -> None:
        """When LLM is unreachable, falls back to raw results without crashing."""
        from src.mcp_server import memory_deep_recall

        # httpx will fail to connect to a non-existent server
        with patch("httpx.post", side_effect=ConnectionError("Connection refused")):
            result = memory_deep_recall("CORS", synthesize=True)
            # Should not crash, should return raw results
            assert "LLM synthesis unavailable" in result
            assert "CORS" in result

    def test_synthesize_false_skips_llm(self, mcp_db: Path) -> None:
        """When synthesize=False, LLM is not called at all."""
        from src.mcp_server import memory_deep_recall

        with patch("httpx.post") as mock_post:
            result = memory_deep_recall("CORS", synthesize=False)
            mock_post.assert_not_called()
            assert "LLM synthesis unavailable" in result

    def test_uses_correct_model(self) -> None:
        """The LLM call uses llama3.3:70b as the model."""
        import ast

        source = Path("src/mcp_server.py").read_text()
        tree = ast.parse(source)

        # Find the memory_deep_recall function and verify llama3.3:70b is used
        assert 'llama3.3:70b' in source
        # Specifically verify it's in the httpx.post call within memory_deep_recall
        func_source = source[source.index("def memory_deep_recall"):source.index("\n@mcp.tool()\ndef memory_find_entity")]
        assert '"model": "llama3.3:70b"' in func_source
