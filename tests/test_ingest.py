"""Tests for src/ingest.py — derive_project, parse_history_file, extract_text_content, source_tool."""

import json
import sqlite3
import tempfile
from pathlib import Path

from src.ingest import (
    derive_project,
    extract_text_content,
    insert_records,
    parse_history_file,
    parse_interaction_jsonl,
    parse_project_jsonl,
)
from src.memory_db import migrate_schema


class TestDeriveProject:
    """derive_project extracts a project path from Claude's encoded directory names."""

    def test_double_hyphen_escaping(self) -> None:
        """Double hyphens in encoded name are literal hyphens in the project path."""
        source = "/home/user/.claude/projects/-home-user-my--project/session.jsonl"
        assert derive_project(source) == "/home/user/my-project"

    def test_simple_path(self) -> None:
        """Simple path without escaping resolves correctly."""
        source = "/home/user/.claude/projects/-home-user-simple/session.jsonl"
        assert derive_project(source) == "/home/user/simple"

    def test_no_projects_dir(self) -> None:
        """Returns None when path does not contain a 'projects' directory."""
        source = "/home/user/.claude/history.jsonl"
        assert derive_project(source) is None


class TestParseHistoryFile:
    """parse_history_file reads ~/.claude/history.jsonl entries."""

    def test_parse_temp_jsonl(self) -> None:
        """Parses a temporary JSONL file and returns expected records."""
        entries = [
            {
                "display": "hello world",
                "timestamp": 1700000000000,
                "project": "/home/user/myproject",
                "sessionId": "sess-001",
            },
            {
                "display": "second prompt",
                "timestamp": 1700000060000,
                "project": "/home/user/myproject",
                "sessionId": "sess-001",
            },
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        records, new_offset = parse_history_file(tmp_path)

        assert len(records) == 2
        assert records[0]["content"] == "hello world"
        assert records[0]["role"] == "user"
        assert records[0]["session_id"] == "sess-001"
        assert records[0]["project"] == "/home/user/myproject"
        assert records[1]["content"] == "second prompt"

        # Offset should be beyond start
        assert new_offset > 0

        # Cleanup
        Path(tmp_path).unlink()


class TestExtractTextContent:
    """extract_text_content handles both string and list-of-blocks formats."""

    def test_string_content(self) -> None:
        """Extracts text when content is a plain string."""
        message = {"content": "Hello, world!"}
        assert extract_text_content(message) == "Hello, world!"

    def test_list_of_blocks(self) -> None:
        """Extracts and joins text from a list of content blocks."""
        message = {
            "content": [
                {"type": "text", "text": "First paragraph."},
                {"type": "tool_use", "name": "some_tool"},
                {"type": "text", "text": "Second paragraph."},
            ]
        }
        result = extract_text_content(message)
        assert result == "First paragraph.\n\nSecond paragraph."

    def test_none_input(self) -> None:
        """Returns None for None or non-dict input."""
        assert extract_text_content(None) is None

    def test_empty_string_content(self) -> None:
        """Returns None for whitespace-only string content."""
        message = {"content": "   "}
        assert extract_text_content(message) is None

    def test_empty_blocks(self) -> None:
        """Returns None when all blocks are non-text."""
        message = {"content": [{"type": "tool_use", "name": "tool"}]}
        assert extract_text_content(message) is None


SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


class TestMigration7SourceTool:
    """Migration 7 adds source_tool TEXT DEFAULT 'claude_code' to messages table."""

    def _make_db(self) -> sqlite3.Connection:
        """Create an in-memory DB from schema.sql (no source_tool column yet)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_PATH.read_text())
        return conn

    def test_column_exists_after_migration(self) -> None:
        """VAL-SCHEMA-001: source_tool column exists after migrate_schema()."""
        conn = self._make_db()
        migrate_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "source_tool" in columns
        conn.close()

    def test_column_default_is_claude_code(self) -> None:
        """VAL-SCHEMA-001: Default value for source_tool is 'claude_code'."""
        conn = self._make_db()
        migrate_schema(conn)
        # Insert a row without specifying source_tool
        conn.execute(
            "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.jsonl", "sess-1", "/proj", "user", "hello", "2024-01-01T00:00:00", "local"),
        )
        conn.commit()
        row = conn.execute("SELECT source_tool FROM messages WHERE session_id='sess-1'").fetchone()
        assert row[0] == "claude_code"
        conn.close()

    def test_existing_rows_get_default(self) -> None:
        """VAL-SCHEMA-002: Existing rows get source_tool='claude_code' via DEFAULT."""
        conn = self._make_db()
        # Insert rows BEFORE migration
        conn.execute(
            "INSERT INTO messages (source_file, session_id, project, role, content, timestamp, machine) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.jsonl", "sess-1", "/proj", "user", "hello", "2024-01-01T00:00:00", "local"),
        )
        conn.commit()
        # Now run migration
        migrate_schema(conn)
        null_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE source_tool IS NULL"
        ).fetchone()[0]
        assert null_count == 0
        row = conn.execute("SELECT source_tool FROM messages").fetchone()
        assert row[0] == "claude_code"
        conn.close()

    def test_idempotent(self) -> None:
        """VAL-SCHEMA-003: Running migrate_schema() twice is safe."""
        conn = self._make_db()
        migrate_schema(conn)
        # Should not raise
        migrate_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "source_tool" in columns
        conn.close()


class TestSourceToolInParsers:
    """Existing parsers include source_tool='claude_code' in their record dicts."""

    def test_parse_history_file_has_source_tool(self) -> None:
        """parse_history_file records include source_tool='claude_code'."""
        entries = [
            {
                "display": "hello world",
                "timestamp": 1700000000000,
                "project": "/home/user/myproject",
                "sessionId": "sess-001",
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        records, _ = parse_history_file(tmp_path)
        assert len(records) == 1
        assert records[0]["source_tool"] == "claude_code"
        Path(tmp_path).unlink()

    def test_parse_project_jsonl_has_source_tool(self) -> None:
        """parse_project_jsonl records include source_tool='claude_code'."""
        entries = [
            {
                "type": "user",
                "sessionId": "sess-001",
                "timestamp": "2024-01-01T00:00:00",
                "message": {"content": "hello"},
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        records, _ = parse_project_jsonl(tmp_path)
        assert len(records) == 1
        assert records[0]["source_tool"] == "claude_code"
        Path(tmp_path).unlink()

    def test_parse_interaction_jsonl_has_source_tool(self) -> None:
        """parse_interaction_jsonl records include source_tool='claude_code'."""
        entries = [
            {
                "ts": "2024-01-01T00:00:00",
                "data": {"prompt": "hello world", "session_id": "sess-001"},
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        records, _ = parse_interaction_jsonl(tmp_path)
        assert len(records) >= 1
        assert records[0]["source_tool"] == "claude_code"
        Path(tmp_path).unlink()


class TestInsertRecordsSourceTool:
    """insert_records() persists the source_tool field to the database."""

    def _make_db(self) -> sqlite3.Connection:
        """Create an in-memory DB from schema.sql with migration applied."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_PATH.read_text())
        migrate_schema(conn)
        return conn

    def test_source_tool_persisted(self) -> None:
        """VAL-INT-002: insert_records writes source_tool to the database."""
        conn = self._make_db()
        records = [
            {
                "source_file": "test.jsonl",
                "session_id": "sess-1",
                "project": "/proj",
                "role": "user",
                "content": "hello",
                "timestamp": "2024-01-01T00:00:00",
                "machine": "local",
                "source_tool": "claude_code",
            },
        ]
        insert_records(conn, records)
        conn.commit()
        row = conn.execute("SELECT source_tool FROM messages").fetchone()
        assert row[0] == "claude_code"
        conn.close()

    def test_source_tool_different_values(self) -> None:
        """insert_records stores different source_tool values correctly."""
        conn = self._make_db()
        records = [
            {
                "source_file": "test1.jsonl",
                "session_id": "sess-1",
                "project": "/proj",
                "role": "user",
                "content": "hello from claude",
                "timestamp": "2024-01-01T00:00:00",
                "machine": "local",
                "source_tool": "claude_code",
            },
            {
                "source_file": "test2.jsonl",
                "session_id": "sess-2",
                "project": "/proj",
                "role": "user",
                "content": "hello from factory",
                "timestamp": "2024-01-01T00:01:00",
                "machine": "local",
                "source_tool": "factory",
            },
            {
                "source_file": "test3.jsonl",
                "session_id": "sess-3",
                "project": "/proj",
                "role": "user",
                "content": "hello from codex",
                "timestamp": "2024-01-01T00:02:00",
                "machine": "local",
                "source_tool": "codex",
            },
        ]
        insert_records(conn, records)
        conn.commit()
        rows = conn.execute(
            "SELECT source_tool, COUNT(*) FROM messages GROUP BY source_tool ORDER BY source_tool"
        ).fetchall()
        result = {row[0]: row[1] for row in rows}
        assert result == {"claude_code": 1, "codex": 1, "factory": 1}
        conn.close()
