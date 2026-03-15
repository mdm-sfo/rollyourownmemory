"""Tests for the web application scaffold (src/web.py)."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


@pytest.fixture
def web_db(tmp_path):
    """Create a temporary SQLite database with schema for web tests."""
    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.close()
    return str(db_path)


@pytest.fixture
def client(web_db):
    """Create a FastAPI TestClient with patched DB path."""
    with patch("src.memory_db.DB_PATH", web_db):
        from src.web import app
        with TestClient(app) as c:
            yield c


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "db_accessible" in data

    def test_health_db_accessible_true(self, client):
        resp = client.get("/api/health")
        data = resp.json()
        assert data["db_accessible"] is True

    def test_health_db_inaccessible(self, client):
        """When the database cannot be reached, db_accessible should be False."""
        with patch("src.web.memory_db.get_conn", side_effect=Exception("no db")):
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["db_accessible"] is False


class TestMainPage:
    """Tests for GET / serving the main HTML page."""

    def test_root_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_root_has_nav_bar(self, client):
        resp = client.get("/")
        assert "<nav" in resp.text

    def test_root_has_search_bar(self, client):
        resp = client.get("/")
        # Should have some kind of search input
        assert 'id="search-input"' in resp.text or 'class="search' in resp.text

    def test_root_has_nav_links(self, client):
        """Nav bar should have links for Search, Facts, Sessions, CLAUDE.md, Context Preview."""
        resp = client.get("/")
        html = resp.text
        assert "Search" in html
        assert "Facts" in html
        assert "Sessions" in html
        assert "CLAUDE.md" in html
        assert "Context Preview" in html

    def test_root_has_mode_toggle(self, client):
        """Should have Search/Ask mode toggle tabs."""
        resp = client.get("/")
        html = resp.text
        assert "Search" in html
        assert "Ask" in html


class TestErrorHandlers:
    """Tests for structured JSON error responses."""

    def test_404_returns_json(self, client):
        resp = client.get("/api/nonexistent-endpoint")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data

    def test_404_not_html(self, client):
        resp = client.get("/api/nonexistent-endpoint")
        assert "application/json" in resp.headers["content-type"]


class TestStaticFiles:
    """Tests for static file serving."""

    def test_css_loads(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_js_loads(self, client):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        # JS content type
        ct = resp.headers["content-type"]
        assert "javascript" in ct or "text/plain" in ct

    def test_no_cdn_in_html(self, client):
        """All assets must be local — zero external CDN references."""
        resp = client.get("/")
        html = resp.text
        # No external CDN domains
        assert "cdnjs.cloudflare.com" not in html
        assert "cdn.jsdelivr.net" not in html
        assert "unpkg.com" not in html
        assert "googleapis.com" not in html
        assert "bootstrapcdn.com" not in html

    def test_no_cdn_in_css(self, client):
        resp = client.get("/static/style.css")
        css = resp.text
        assert "cdnjs.cloudflare.com" not in css
        assert "fonts.googleapis.com" not in css

    def test_no_cdn_in_js(self, client):
        resp = client.get("/static/app.js")
        js = resp.text
        assert "cdnjs.cloudflare.com" not in js
        assert "cdn.jsdelivr.net" not in js
