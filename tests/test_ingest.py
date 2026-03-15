"""Tests for src/ingest.py — derive_project, parse_history_file, extract_text_content."""

import json
import tempfile
from pathlib import Path

from src.ingest import derive_project, extract_text_content, parse_history_file


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
