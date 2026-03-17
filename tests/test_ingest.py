"""Tests for src/ingest.py — derive_project, parse_history_file, extract_text_content, source_tool,
Factory parser, Codex session parser, Codex history parser."""

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
    parse_factory_jsonl,
    parse_codex_session_jsonl,
    parse_codex_history,
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


# ── Factory Parser Tests ──────────────────────────────────────────────────────


class TestParseFactoryJsonl:
    """parse_factory_jsonl extracts user/assistant messages from Factory JSONL sessions."""

    def _write_jsonl(self, lines: list[dict]) -> str:
        """Helper: write list of dicts as JSONL to a temp file, return path."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
            return f.name

    def test_parse_factory_messages(self) -> None:
        """VAL-FACTORY-002: Extracts user and assistant messages from type='message' records."""
        lines = [
            {"type": "session_start", "id": "sess-factory-001", "cwd": "/home/user/proj"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Hello factory"}]},
            },
            {
                "type": "message",
                "id": "msg-2",
                "timestamp": "2026-03-15T10:01:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hi there!"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, offset = parse_factory_jsonl(path)
            assert len(records) == 2
            assert records[0]["role"] == "user"
            assert records[0]["content"] == "Hello factory"
            assert records[0]["timestamp"] == "2026-03-15T10:00:00.000Z"
            assert records[1]["role"] == "assistant"
            assert records[1]["content"] == "Hi there!"
            assert offset > 0
        finally:
            Path(path).unlink()

    def test_factory_skips_non_message_types(self) -> None:
        """VAL-FACTORY-002: session_start, session_end, todo_state are skipped."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user/proj"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            },
            {
                "type": "todo_state",
                "id": "todo-1",
                "timestamp": "2026-03-15T10:00:01.000Z",
                "todos": {"todos": "1. [in_progress] Do something"},
            },
            {"type": "session_end", "id": "end-1"},
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "Hello"
        finally:
            Path(path).unlink()

    def test_factory_session_id_from_session_start(self) -> None:
        """VAL-FACTORY-006: session_id is extracted from session_start record's 'id' field."""
        lines = [
            {"type": "session_start", "id": "abcd-1234-factory-session", "cwd": "/home/user"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Test"}]},
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["session_id"] == "abcd-1234-factory-session"
        finally:
            Path(path).unlink()

    def test_factory_skips_thinking_blocks(self) -> None:
        """VAL-FACTORY-002: Thinking content blocks are skipped (only text blocks extracted)."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "signature": "xxx", "thinking": "encrypted"},
                        {"type": "text", "text": "Visible response"},
                    ],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "Visible response"
        finally:
            Path(path).unlink()

    def test_factory_source_tool_tagging(self) -> None:
        """VAL-FACTORY-004: All Factory records have source_tool='factory'."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert all(r["source_tool"] == "factory" for r in records)
        finally:
            Path(path).unlink()

    def test_factory_content_extraction_multi_block(self) -> None:
        """VAL-FACTORY-005: Multi-block content extraction with tool_use/tool_result mixed in."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "First part"},
                        {"type": "tool_use", "id": "tool1", "name": "Read", "input": {}},
                        {"type": "text", "text": "Second part"},
                    ],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "First part\n\nSecond part"
        finally:
            Path(path).unlink()

    def test_factory_project_from_session_start_cwd(self) -> None:
        """VAL-FACTORY-003: Factory project is derived from session_start.cwd, not derive_project()."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user/my-project"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["project"] == "/home/user/my-project"
        finally:
            Path(path).unlink()

    def test_factory_project_none_without_session_start(self) -> None:
        """Factory project is None if no session_start record exists."""
        lines = [
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["project"] is None
        finally:
            Path(path).unlink()

    def test_factory_malformed_lines_skipped(self) -> None:
        """VAL-INT-006: Malformed JSONL lines are skipped gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "session_start", "id": "sess-001", "cwd": "/home/user"}) + "\n")
            f.write("this is not valid json\n")
            f.write(json.dumps({
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "After bad line"}]},
            }) + "\n")
            f.write("{truncated\n")
            path = f.name

        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "After bad line"
        finally:
            Path(path).unlink()

    def test_factory_offset_tracking(self) -> None:
        """Offset tracking: parsing from offset skips already-processed data."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "First"}]},
            },
            {
                "type": "message",
                "id": "msg-2",
                "timestamp": "2026-03-15T10:01:00.000Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Second"}]},
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records1, offset1 = parse_factory_jsonl(path)
            assert len(records1) == 2
            # Reading from end offset should give 0 new records
            records2, offset2 = parse_factory_jsonl(path, offset=offset1)
            assert len(records2) == 0
            assert offset2 == offset1
        finally:
            Path(path).unlink()

    def test_factory_skips_only_thinking_messages(self) -> None:
        """Messages with ONLY thinking blocks (no text) are skipped."""
        lines = [
            {"type": "session_start", "id": "sess-001", "cwd": "/home/user"},
            {
                "type": "message",
                "id": "msg-1",
                "timestamp": "2026-03-15T10:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "signature": "xxx", "thinking": "encrypted"},
                    ],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_factory_jsonl(path)
            assert len(records) == 0
        finally:
            Path(path).unlink()


# ── Codex Session Parser Tests ────────────────────────────────────────────────


class TestParseCodexSessionJsonl:
    """parse_codex_session_jsonl extracts messages from Codex CLI session JSONL."""

    def _write_jsonl(self, lines: list[dict]) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
            return f.name

    def test_parse_codex_session_messages(self) -> None:
        """VAL-CODEX-002: Extracts user/assistant messages from response_item records."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user/project"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hello codex"}],
                },
            },
            {
                "timestamp": "2026-03-09T16:31:25.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hi from codex!"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, offset = parse_codex_session_jsonl(path)
            assert len(records) == 2
            assert records[0]["role"] == "user"
            assert records[0]["content"] == "Hello codex"
            assert records[1]["role"] == "assistant"
            assert records[1]["content"] == "Hi from codex!"
            assert offset > 0
        finally:
            Path(path).unlink()

    def test_codex_skips_developer_and_non_message(self) -> None:
        """VAL-CODEX-002: Developer role and non-message payload types are skipped."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "System instructions"}],
                },
            },
            {
                "timestamp": "2026-03-09T16:31:21.000Z",
                "type": "response_item",
                "payload": {"type": "reasoning", "text": "thinking..."},
            },
            {
                "timestamp": "2026-03-09T16:31:22.000Z",
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell", "arguments": "{}"},
            },
            {
                "timestamp": "2026-03-09T16:31:23.000Z",
                "type": "response_item",
                "payload": {"type": "function_call_output", "output": "result"},
            },
            {
                "timestamp": "2026-03-09T16:31:24.000Z",
                "type": "response_item",
                "payload": {"type": "web_search_call", "query": "test"},
            },
            {
                "timestamp": "2026-03-09T16:31:25.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Real user message"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "Real user message"
        finally:
            Path(path).unlink()

    def test_codex_both_phases_ingested(self) -> None:
        """VAL-CODEX-003: Both commentary and final_answer phases are ingested."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "Commentary phase"}],
                },
            },
            {
                "timestamp": "2026-03-09T16:31:25.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Final answer phase"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert len(records) == 2
            assert records[0]["content"] == "Commentary phase"
            assert records[1]["content"] == "Final answer phase"
        finally:
            Path(path).unlink()

    def test_codex_content_blocks(self) -> None:
        """VAL-CODEX-004: input_text and output_text content blocks extracted correctly."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "First part"},
                        {"type": "input_text", "text": "Second part"},
                    ],
                },
            },
            {
                "timestamp": "2026-03-09T16:31:25.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Response A"},
                        {"type": "output_text", "text": "Response B"},
                    ],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert len(records) == 2
            assert records[0]["content"] == "First part\n\nSecond part"
            assert records[1]["content"] == "Response A\n\nResponse B"
        finally:
            Path(path).unlink()

    def test_codex_session_id_from_meta(self) -> None:
        """VAL-CODEX-008: session_id extracted from session_meta.payload.id."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "unique-codex-uuid", "cwd": "/home/user"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Test"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert records[0]["session_id"] == "unique-codex-uuid"
        finally:
            Path(path).unlink()

    def test_codex_project_from_cwd(self) -> None:
        """VAL-CODEX-005: Project derived from session_meta.payload.cwd."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user/my-project"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Test"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert records[0]["project"] == "/home/user/my-project"
        finally:
            Path(path).unlink()

    def test_codex_source_tool_tagging(self) -> None:
        """VAL-CODEX-007: All Codex session records have source_tool='codex'."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hi"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert all(r["source_tool"] == "codex" for r in records)
        finally:
            Path(path).unlink()

    def test_codex_skips_event_msg_and_turn_context(self) -> None:
        """Non-response_item types (event_msg, turn_context) are skipped."""
        lines = [
            {
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user"},
            },
            {
                "timestamp": "2026-03-09T16:31:20.000Z",
                "type": "event_msg",
                "payload": {"type": "task_started"},
            },
            {
                "timestamp": "2026-03-09T16:31:21.000Z",
                "type": "turn_context",
                "payload": {"turn_id": "turn-1"},
            },
            {
                "timestamp": "2026-03-09T16:31:25.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Real message"}],
                },
            },
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_session_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "Real message"
        finally:
            Path(path).unlink()

    def test_codex_malformed_lines_skipped(self) -> None:
        """VAL-INT-006: Malformed JSONL lines in Codex sessions are skipped gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({
                "timestamp": "2026-03-09T16:31:19.000Z",
                "type": "session_meta",
                "payload": {"id": "codex-sess-001", "cwd": "/home/user"},
            }) + "\n")
            f.write("not valid json!!!\n")
            f.write(json.dumps({
                "timestamp": "2026-03-09T16:31:25.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "After bad line"}],
                },
            }) + "\n")
            path = f.name

        try:
            records, _ = parse_codex_session_jsonl(path)
            assert len(records) == 1
            assert records[0]["content"] == "After bad line"
        finally:
            Path(path).unlink()


# ── Codex History Parser Tests ────────────────────────────────────────────────


class TestParseCodexHistory:
    """parse_codex_history parses ~/.codex/history.jsonl with epoch-second timestamps."""

    def _write_jsonl(self, lines: list[dict]) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
            return f.name

    def test_parse_codex_history(self) -> None:
        """VAL-CODEX-006: Parses history entries with epoch-second timestamps."""
        lines = [
            {"session_id": "sess-001", "ts": 1771860195, "text": "how do i keep this machine from sleeping?"},
            {"session_id": "sess-001", "ts": 1771860448, "text": "nvidia dgx spark"},
        ]
        path = self._write_jsonl(lines)
        try:
            records, offset = parse_codex_history(path)
            assert len(records) == 2
            assert records[0]["content"] == "how do i keep this machine from sleeping?"
            assert records[0]["role"] == "user"
            assert records[0]["session_id"] == "sess-001"
            assert records[0]["source_tool"] == "codex"
            # Timestamp should be ISO 8601 (converted from epoch seconds)
            assert "2026" in records[0]["timestamp"] or "2025" in records[0]["timestamp"] or "1970" not in records[0]["timestamp"]
            assert offset > 0
        finally:
            Path(path).unlink()

    def test_codex_history_timestamp_is_seconds_not_ms(self) -> None:
        """VAL-CODEX-006: Epoch seconds (not milliseconds) are correctly converted to ISO."""
        from datetime import datetime
        lines = [
            {"session_id": "sess-001", "ts": 1700000000, "text": "test"},
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_history(path)
            ts = records[0]["timestamp"]
            # 1700000000 = 2023-11-14T22:13:20 (UTC approx)
            expected = datetime.fromtimestamp(1700000000).isoformat()
            assert ts == expected
        finally:
            Path(path).unlink()

    def test_codex_history_source_tool(self) -> None:
        """VAL-CODEX-007: Codex history records have source_tool='codex'."""
        lines = [
            {"session_id": "sess-001", "ts": 1700000000, "text": "hello"},
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_history(path)
            assert records[0]["source_tool"] == "codex"
        finally:
            Path(path).unlink()

    def test_codex_history_malformed_lines(self) -> None:
        """VAL-INT-006: Malformed lines in codex history are skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"session_id": "s1", "ts": 1700000000, "text": "good"}) + "\n")
            f.write("bad json line\n")
            f.write(json.dumps({"session_id": "s1", "ts": 1700000001, "text": "also good"}) + "\n")
            path = f.name

        try:
            records, _ = parse_codex_history(path)
            assert len(records) == 2
        finally:
            Path(path).unlink()

    def test_codex_history_empty_text_skipped(self) -> None:
        """Empty text entries are skipped."""
        lines = [
            {"session_id": "sess-001", "ts": 1700000000, "text": ""},
            {"session_id": "sess-001", "ts": 1700000001, "text": "   "},
            {"session_id": "sess-001", "ts": 1700000002, "text": "real prompt"},
        ]
        path = self._write_jsonl(lines)
        try:
            records, _ = parse_codex_history(path)
            assert len(records) == 1
            assert records[0]["content"] == "real prompt"
        finally:
            Path(path).unlink()


# ── derive_project Sessions Path Tests ────────────────────────────────────────


class TestDeriveProjectSessions:
    """derive_project is for Claude Code paths only — does NOT handle Factory 'sessions' paths."""

    def test_factory_sessions_path_returns_none(self) -> None:
        """Factory 'sessions' paths are NOT handled by derive_project (uses cwd instead)."""
        source = "/home/user/.factory/sessions/-home-user-my--project/abc.jsonl"
        assert derive_project(source) is None

    def test_still_works_for_claude_projects(self) -> None:
        """Existing 'projects' paths still work."""
        source = "/home/user/.claude/projects/-home-user-simple/session.jsonl"
        assert derive_project(source) == "/home/user/simple"

    def test_still_works_for_ec2_projects(self) -> None:
        """Wormhole ec2-projects paths still work."""
        source = "/home/user/wormhole/claude-logs/ec2-projects/-home-user-myapp/session.jsonl"
        assert derive_project(source) == "/home/user/myapp"
