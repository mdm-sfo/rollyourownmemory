"""Shared fixtures for claude-memory tests."""

import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


@pytest.fixture
def db() -> sqlite3.Connection:
    """In-memory SQLite connection initialised from schema.sql.

    Returns a raw connection (no row_factory) so tests can use either
    plain tuples or set row_factory themselves.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()
