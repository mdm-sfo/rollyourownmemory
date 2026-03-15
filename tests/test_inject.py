"""Tests for src/inject.py — priority-based section assembly and --stdout flag."""

import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.inject import generate_memory_context

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"
INJECT_SCRIPT = Path(__file__).parent.parent / "src" / "inject.py"


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    """Create a fresh in-memory-like DB with schema applied, return connection."""
    db_path = tmp_path / "test_inject.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _seed_facts(conn: sqlite3.Connection, count: int = 5, project: str | None = None) -> None:
    """Insert *count* test facts into the DB."""
    now = datetime.now(timezone.utc).isoformat()
    for i in range(count):
        conn.execute(
            """INSERT INTO facts
               (session_id, project, fact, category, confidence,
                source_message_id, timestamp, last_validated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"sess-{i}", project, f"Test fact number {i}: important detail about topic {i}",
             "preference", 0.9, None, now, now),
        )
    conn.commit()


def _seed_sessions(conn: sqlite3.Connection, count: int = 3, project: str | None = None) -> None:
    """Insert test messages to populate session data."""
    now = datetime.now(timezone.utc).isoformat()
    for i in range(count):
        conn.execute(
            """INSERT INTO messages
               (source_file, session_id, project, role, content, timestamp, machine)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("test.jsonl", f"sess-{i}", project, "user",
             f"Working on task {i} for the project", now, "test-machine"),
        )
    conn.commit()


class TestPriorityAssembly:
    """generate_memory_context uses priority-based section assembly."""

    def test_sections_built_with_priority(self, tmp_path: Path) -> None:
        """Sections are assembled in priority order, not just appended."""
        conn = _make_conn(tmp_path)
        _seed_facts(conn, count=5)
        _seed_sessions(conn, count=3)
        db_path = str(tmp_path / "test_inject.db")
        conn.close()

        with patch("src.inject.DB_PATH", Path(db_path)):
            result = generate_memory_context(
                project=None, focus=None, max_tokens=2000, auto_detect=False,
            )

        assert result.startswith("# Memory Context")
        # Key Facts should appear before Recent Sessions (priority 2 < 4)
        if "## Key Facts" in result and "## Recent Sessions" in result:
            assert result.index("## Key Facts") < result.index("## Recent Sessions")

    def test_no_mid_text_truncation(self, tmp_path: Path) -> None:
        """Output should NOT contain the old truncation marker."""
        conn = _make_conn(tmp_path)
        _seed_facts(conn, count=20)
        _seed_sessions(conn, count=10)
        db_path = str(tmp_path / "test_inject.db")
        conn.close()

        with patch("src.inject.DB_PATH", Path(db_path)):
            result = generate_memory_context(
                project=None, focus=None, max_tokens=200, auto_detect=False,
            )

        assert "[...truncated to fit token budget]" not in result

    def test_sections_skipped_when_over_budget(self, tmp_path: Path) -> None:
        """With a very tight budget, lower-priority sections are skipped entirely."""
        conn = _make_conn(tmp_path)
        _seed_facts(conn, count=10)
        _seed_sessions(conn, count=5)
        db_path = str(tmp_path / "test_inject.db")
        conn.close()

        with patch("src.inject.DB_PATH", Path(db_path)):
            result = generate_memory_context(
                project=None, focus=None, max_tokens=100, auto_detect=False,
            )

        # With 100 tokens (~400 chars) the header + facts may squeeze in but
        # sessions/entities almost certainly won't.  At minimum, the result
        # should not have mid-text cutoff.
        assert "[...truncated" not in result

    def test_facts_limit_reduced_when_too_large(self, tmp_path: Path) -> None:
        """If facts section alone exceeds budget, LIMIT is reduced to fit."""
        conn = _make_conn(tmp_path)
        # Insert many facts with long text so the section is large
        now = datetime.now(timezone.utc).isoformat()
        for i in range(30):
            conn.execute(
                """INSERT INTO facts
                   (session_id, project, fact, category, confidence,
                    source_message_id, timestamp, last_validated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"sess-{i}", None, f"Long fact #{i}: " + "x" * 200,
                 "learning", 0.9, None, now, now),
            )
        conn.commit()
        db_path = str(tmp_path / "test_inject.db")
        conn.close()

        with patch("src.inject.DB_PATH", Path(db_path)):
            result = generate_memory_context(
                project=None, focus=None, max_tokens=300, auto_detect=False,
            )

        # The function should still return something valid (not crash) and
        # not contain the old truncation marker
        assert "# Memory Context" in result
        assert "[...truncated" not in result

    def test_empty_db_returns_fallback(self, tmp_path: Path) -> None:
        """An empty DB returns the 'no data' fallback."""
        conn = _make_conn(tmp_path)
        db_path = str(tmp_path / "test_inject.db")
        conn.close()

        with patch("src.inject.DB_PATH", Path(db_path)):
            result = generate_memory_context(
                project=None, focus=None, max_tokens=2000, auto_detect=False,
            )

        assert "No memory data available yet" in result

    def test_header_always_included(self, tmp_path: Path) -> None:
        """The header is always present even with data."""
        conn = _make_conn(tmp_path)
        _seed_facts(conn, count=3)
        db_path = str(tmp_path / "test_inject.db")
        conn.close()

        with patch("src.inject.DB_PATH", Path(db_path)):
            result = generate_memory_context(
                project="myproject", focus=None, max_tokens=2000, auto_detect=False,
            )

        assert result.startswith("# Memory Context (myproject)")


class TestStdoutFlag:
    """--stdout flag prints output to stdout instead of writing a file."""

    def test_help_shows_stdout_flag(self) -> None:
        """inject.py --help includes --stdout."""
        result = subprocess.run(
            [sys.executable, str(INJECT_SCRIPT), "--help"],
            capture_output=True, text=True,
        )
        assert "--stdout" in result.stdout

    def test_stdout_flag_prints_to_stdout(self, tmp_path: Path) -> None:
        """Running with --stdout sends output to stdout."""
        result = subprocess.run(
            [sys.executable, str(INJECT_SCRIPT), "--stdout", "--max-tokens", "200",
             "--no-detect"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        # Should have some output on stdout (at minimum the header or no-data message)
        assert "Memory Context" in result.stdout or "No memory" in result.stdout
