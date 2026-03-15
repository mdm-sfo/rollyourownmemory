"""Tests for the web application (src/web.py).

Tests cover:
- Scaffold endpoints (health, static files, error handlers)
- Search API (FTS messages, facts, sessions, semantic fallback)
- Fact inspect endpoint
- Facts CRUD (list, update, delete)
- Session list and detail endpoints
- Ask mode SSE streaming endpoint
- CLAUDE.md editor endpoints (GET, PUT)
- Context preview endpoint
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


# --- Fixtures ---

@pytest.fixture
def web_db(tmp_path):
    """Create a temporary SQLite database with schema for web tests."""
    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.close()
    return str(db_path)


@pytest.fixture
def seeded_db(tmp_path):
    """Create a temporary SQLite database with test data."""
    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text())

    # Insert test messages
    conn.execute(
        "INSERT INTO messages (id, source_file, session_id, project, role, content, timestamp, machine) "
        "VALUES (1, 'test.jsonl', 'sess-001', 'kalshi', 'user', 'How do I deploy kalshi to production?', '2024-01-15T10:00:00', 'laptop')"
    )
    conn.execute(
        "INSERT INTO messages (id, source_file, session_id, project, role, content, timestamp, machine) "
        "VALUES (2, 'test.jsonl', 'sess-001', 'kalshi', 'assistant', 'You can deploy using docker compose.', '2024-01-15T10:01:00', 'laptop')"
    )
    conn.execute(
        "INSERT INTO messages (id, source_file, session_id, project, role, content, timestamp, machine) "
        "VALUES (3, 'test.jsonl', 'sess-002', 'memory', 'user', 'Search for <script>alert(1)</script> test', '2024-01-16T12:00:00', 'desktop')"
    )

    # Insert test facts
    conn.execute(
        "INSERT INTO facts (id, session_id, project, fact, category, confidence, source_message_id, timestamp, compressed_details) "
        "VALUES (1, 'sess-001', 'kalshi', 'Kalshi uses docker compose for deployment', 'tool', 0.9, 2, '2024-01-15T10:01:00', 'specific docker commands')"
    )
    conn.execute(
        "INSERT INTO facts (id, session_id, project, fact, category, confidence, source_message_id, timestamp, compressed_details) "
        "VALUES (2, 'sess-001', 'kalshi', 'Kalshi project uses Python 3.12', 'context', 0.8, 1, '2024-01-15T10:00:00', NULL)"
    )
    conn.execute(
        "INSERT INTO facts (id, session_id, project, fact, category, confidence, source_message_id, timestamp, compressed_details) "
        "VALUES (3, 'sess-002', 'memory', 'Memory system uses SQLite FTS5', 'tool', 0.95, 3, '2024-01-16T12:00:00', NULL)"
    )

    # Insert test entities
    conn.execute(
        "INSERT INTO entities (id, name, entity_type, first_seen, last_seen, mention_count) "
        "VALUES (1, 'docker', 'tool', '2024-01-15', '2024-01-15', 5)"
    )
    conn.execute(
        "INSERT INTO entity_mentions (entity_id, message_id, session_id, timestamp) "
        "VALUES (1, 2, 'sess-001', '2024-01-15T10:01:00')"
    )

    # Rebuild FTS indexes
    conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def facts_db(tmp_path):
    """Create a DB with many facts for pagination / CRUD testing."""
    db_path = tmp_path / "facts_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text())

    # Insert a message for source reference
    conn.execute(
        "INSERT INTO messages (id, source_file, session_id, project, role, content, timestamp, machine) "
        "VALUES (1, 'test.jsonl', 'sess-001', 'kalshi', 'user', 'test message', '2024-01-15T10:00:00', 'laptop')"
    )

    categories = ['preference', 'decision', 'learning', 'context', 'tool', 'pattern']
    projects = ['kalshi', 'memory', 'webapp']

    for i in range(1, 16):
        cat = categories[i % len(categories)]
        proj = projects[i % len(projects)]
        conf = round(0.1 + (i % 10) * 0.1, 1)  # 0.2..1.0 cycling
        ts = f"2024-01-{15 + (i % 5):02d}T{10 + i:02d}:00:00"
        conn.execute(
            "INSERT INTO facts (id, session_id, project, fact, category, confidence, "
            "source_message_id, timestamp, compressed_details) "
            f"VALUES ({i}, 'sess-001', '{proj}', 'Test fact number {i} about {cat}', "
            f"'{cat}', {conf}, 1, '{ts}', NULL)"
        )

    conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def client(web_db):
    """Create a FastAPI TestClient with patched DB path (empty DB)."""
    with patch("src.memory_db.DB_PATH", web_db):
        from src.web import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def seeded_client(seeded_db):
    """Create a FastAPI TestClient with seeded test data."""
    with patch("src.memory_db.DB_PATH", seeded_db):
        from src.web import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def facts_client(facts_db):
    """Create a FastAPI TestClient with many facts for CRUD testing."""
    with patch("src.memory_db.DB_PATH", facts_db):
        from src.web import app
        with TestClient(app) as c:
            yield c


# --- Scaffold Tests (existing) ---

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
        ct = resp.headers["content-type"]
        assert "javascript" in ct or "text/plain" in ct

    def test_no_cdn_in_html(self, client):
        """All assets must be local — zero external CDN references."""
        resp = client.get("/")
        html = resp.text
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


# --- Search API Tests ---

class TestSearchEndpoint:
    """Tests for GET /api/search."""

    def test_search_empty_query(self, client):
        """Empty query returns empty results with correct structure."""
        resp = client.get("/api/search?q=")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["facts"] == []
        assert data["sessions"] == []
        assert data["semantic"] == []
        assert "timing_ms" in data

    def test_search_no_query_param(self, client):
        """Missing q param defaults to empty string."""
        resp = client.get("/api/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []

    def test_search_fts_returns_messages(self, seeded_client):
        """FTS search returns matching messages."""
        resp = seeded_client.get("/api/search?q=kalshi")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) > 0
        # Check message structure
        msg = data["messages"][0]
        assert "id" in msg
        assert "content" in msg
        assert "timestamp" in msg
        assert "project" in msg
        assert "role" in msg

    def test_search_returns_facts(self, seeded_client):
        """Search returns matching facts."""
        resp = seeded_client.get("/api/search?q=kalshi")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["facts"]) > 0
        fact = data["facts"][0]
        assert "id" in fact
        assert "fact" in fact
        assert "category" in fact
        assert "confidence" in fact

    def test_search_returns_sessions(self, seeded_client):
        """Search returns matching sessions."""
        resp = seeded_client.get("/api/search?q=kalshi")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) > 0
        sess = data["sessions"][0]
        assert "session_id" in sess
        assert "project" in sess
        assert "msg_count" in sess

    def test_search_has_timing(self, seeded_client):
        """Search returns timing_ms."""
        resp = seeded_client.get("/api/search?q=kalshi")
        data = resp.json()
        assert "timing_ms" in data
        assert isinstance(data["timing_ms"], (int, float))

    def test_search_project_filter(self, seeded_client):
        """Search with project filter narrows results."""
        resp = seeded_client.get("/api/search?q=deploy&project=kalshi")
        assert resp.status_code == 200
        data = resp.json()
        for msg in data["messages"]:
            assert "kalshi" in (msg.get("project") or "").lower()

    def test_search_fts_syntax_error(self, seeded_client):
        """Invalid FTS5 syntax returns gracefully, not 500."""
        resp = seeded_client.get("/api/search?q=" + '"unclosed')
        assert resp.status_code == 200
        data = resp.json()
        # Should not crash — empty results are fine
        assert "messages" in data
        assert "facts" in data

    def test_search_limit_param(self, seeded_client):
        """Limit parameter is respected."""
        resp = seeded_client.get("/api/search?q=kalshi&limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) <= 1

    def test_search_semantic_fallback(self, seeded_client):
        """When FAISS/sentence-transformers unavailable, semantic returns empty list."""
        with patch("src.web._semantic_search", return_value=[]):
            resp = seeded_client.get("/api/search?q=deployment&type=all")
            assert resp.status_code == 200
            data = resp.json()
            assert data["semantic"] == []
            # FTS results should still work
            assert "messages" in data

    def test_search_default_type_is_fts_only(self, seeded_client):
        """Default search (no type param) does NOT call semantic search."""
        with patch("src.web._semantic_search") as mock_semantic:
            mock_semantic.return_value = []
            resp = seeded_client.get("/api/search?q=kalshi")
            assert resp.status_code == 200
            data = resp.json()
            assert data["semantic"] == []
            mock_semantic.assert_not_called()

    def test_search_type_fts_skips_semantic(self, seeded_client):
        """type=fts explicitly skips semantic search."""
        with patch("src.web._semantic_search") as mock_semantic:
            mock_semantic.return_value = []
            resp = seeded_client.get("/api/search?q=kalshi&type=fts")
            assert resp.status_code == 200
            data = resp.json()
            assert data["semantic"] == []
            mock_semantic.assert_not_called()

    def test_search_type_all_includes_semantic(self, seeded_client):
        """type=all includes semantic search results."""
        mock_results = [{"id": 99, "session_id": "s1", "project": "test",
                         "role": "user", "content": "mock", "timestamp": "2024-01-01",
                         "score": 0.95}]
        with patch("src.web._semantic_search", return_value=mock_results) as mock_semantic:
            resp = seeded_client.get("/api/search?q=kalshi&type=all")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["semantic"]) == 1
            assert data["semantic"][0]["score"] == 0.95
            mock_semantic.assert_called_once()

    def test_search_type_semantic_includes_semantic(self, seeded_client):
        """type=semantic also triggers semantic search."""
        mock_results = [{"id": 99, "session_id": "s1", "project": "test",
                         "role": "user", "content": "mock", "timestamp": "2024-01-01",
                         "score": 0.85}]
        with patch("src.web._semantic_search", return_value=mock_results) as mock_semantic:
            resp = seeded_client.get("/api/search?q=kalshi&type=semantic")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["semantic"]) == 1
            mock_semantic.assert_called_once()

    def test_search_returns_json_content_type(self, seeded_client):
        """Search endpoint returns JSON content type."""
        resp = seeded_client.get("/api/search?q=kalshi")
        assert "application/json" in resp.headers["content-type"]

    def test_search_no_results(self, seeded_client):
        """Search with no matching term returns empty arrays."""
        resp = seeded_client.get("/api/search?q=xyznonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["facts"] == []
        assert data["sessions"] == []


# --- Fact Inspect Tests ---

class TestFactInspectEndpoint:
    """Tests for GET /api/facts/{id}."""

    def test_fact_exists(self, seeded_client):
        """Existing fact returns full details."""
        resp = seeded_client.get("/api/facts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert data["fact"] == "Kalshi uses docker compose for deployment"
        assert data["category"] == "tool"
        assert data["confidence"] == 0.9
        assert data["project"] == "kalshi"

    def test_fact_has_source_message(self, seeded_client):
        """Fact detail includes source message."""
        resp = seeded_client.get("/api/facts/1")
        data = resp.json()
        assert data["source_message"] is not None
        assert "content" in data["source_message"]
        assert "role" in data["source_message"]

    def test_fact_has_siblings(self, seeded_client):
        """Fact detail includes sibling facts from same session."""
        resp = seeded_client.get("/api/facts/1")
        data = resp.json()
        assert "siblings" in data
        assert len(data["siblings"]) > 0
        sibling = data["siblings"][0]
        assert "id" in sibling
        assert "fact" in sibling

    def test_fact_has_entities(self, seeded_client):
        """Fact detail includes entities from same session."""
        resp = seeded_client.get("/api/facts/1")
        data = resp.json()
        assert "entities" in data
        assert len(data["entities"]) > 0
        entity = data["entities"][0]
        assert "name" in entity
        assert "entity_type" in entity

    def test_fact_has_compressed_details(self, seeded_client):
        """Fact with compressed_details shows them."""
        resp = seeded_client.get("/api/facts/1")
        data = resp.json()
        assert data["compressed_details"] == "specific docker commands"

    def test_fact_not_found(self, seeded_client):
        """Non-existent fact ID returns 404 JSON."""
        resp = seeded_client.get("/api/facts/999999")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data

    def test_fact_not_found_is_json(self, seeded_client):
        """404 for facts returns JSON, not HTML."""
        resp = seeded_client.get("/api/facts/999999")
        assert "application/json" in resp.headers["content-type"]


# --- Session Endpoints Tests ---

class TestSessionListEndpoint:
    """Tests for GET /api/sessions."""

    def test_session_list(self, seeded_client):
        """Returns list of recent sessions."""
        resp = seeded_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert len(data["sessions"]) > 0

    def test_session_list_structure(self, seeded_client):
        """Each session has expected fields."""
        resp = seeded_client.get("/api/sessions")
        data = resp.json()
        sess = data["sessions"][0]
        assert "session_id" in sess
        assert "project" in sess
        assert "first_msg" in sess
        assert "last_msg" in sess
        assert "msg_count" in sess

    def test_session_list_project_filter(self, seeded_client):
        """Project filter narrows session list."""
        resp = seeded_client.get("/api/sessions?project=kalshi")
        assert resp.status_code == 200
        data = resp.json()
        for sess in data["sessions"]:
            assert "kalshi" in (sess.get("project") or "").lower()

    def test_session_list_empty_project(self, seeded_client):
        """Filtering by non-existent project returns empty list."""
        resp = seeded_client.get("/api/sessions?project=nonexistent123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == []

    def test_session_list_limit(self, seeded_client):
        """Limit parameter works."""
        resp = seeded_client.get("/api/sessions?limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) <= 1


class TestSessionDetailEndpoint:
    """Tests for GET /api/sessions/{id}."""

    def test_session_detail(self, seeded_client):
        """Returns session messages in chronological order."""
        resp = seeded_client.get("/api/sessions/sess-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-001"
        assert "messages" in data
        assert len(data["messages"]) == 2

    def test_session_detail_message_structure(self, seeded_client):
        """Messages have expected fields."""
        resp = seeded_client.get("/api/sessions/sess-001")
        data = resp.json()
        msg = data["messages"][0]
        assert "id" in msg
        assert "role" in msg
        assert "content" in msg
        assert "timestamp" in msg

    def test_session_detail_chronological(self, seeded_client):
        """Messages are in chronological order."""
        resp = seeded_client.get("/api/sessions/sess-001")
        data = resp.json()
        timestamps = [m["timestamp"] for m in data["messages"]]
        assert timestamps == sorted(timestamps)

    def test_session_not_found(self, seeded_client):
        """Non-existent session returns 404 JSON."""
        resp = seeded_client.get("/api/sessions/nonexistent-session")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data

    def test_session_detail_has_project(self, seeded_client):
        """Session detail includes project."""
        resp = seeded_client.get("/api/sessions/sess-001")
        data = resp.json()
        assert data["project"] == "kalshi"


# --- Frontend Tests ---

class TestFrontendXSS:
    """Tests for XSS safety in the frontend JavaScript."""

    def test_app_js_has_escape_function(self, client):
        """app.js must include the escapeHtml function."""
        resp = client.get("/static/app.js")
        assert "escapeHtml" in resp.text

    def test_app_js_escapes_in_rendering(self, client):
        """app.js must use escapeHtml when rendering content."""
        resp = client.get("/static/app.js")
        js = resp.text
        # Check that escapeHtml is called in rendering functions
        assert js.count("escapeHtml") > 5  # Used multiple times


# --- Ask Mode SSE Endpoint Tests ---

class TestAskEndpoint:
    """Tests for GET /api/ask — SSE streaming with LLM synthesis."""

    def _parse_sse_events(self, response_text):
        """Parse SSE text into list of (event_type, data) tuples."""
        events = []
        current_event = None
        current_data = []
        for line in response_text.split("\n"):
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: "):
                current_data.append(line[6:])
            elif line.strip() == "" and (current_event or current_data):
                data = "\n".join(current_data)
                events.append((current_event or "message", data))
                current_event = None
                current_data = []
        # Handle trailing event without blank line
        if current_event or current_data:
            data = "\n".join(current_data)
            events.append((current_event or "message", data))
        return events

    def test_ask_returns_sse_content_type(self, seeded_client):
        """Ask endpoint returns text/event-stream content type."""
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock streaming response (ollama /api/generate format)
            mock_response = AsyncMock()
            mock_response.status_code = 200

            async def mock_aiter():
                yield '{"response":"Hello","done":false}'
                yield '{"response":" world","done":false}'
                yield '{"response":"","done":true}'

            mock_response.aiter_lines = mock_aiter
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=kalshi")
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

    def test_ask_empty_query_returns_error(self, seeded_client):
        """Empty query returns an error SSE event."""
        resp = seeded_client.get("/api/ask?q=")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        events = self._parse_sse_events(resp.text)
        # Should have an error event
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) > 0

    def test_ask_ollama_unavailable_returns_error(self, seeded_client):
        """When ollama is unavailable, returns an error SSE event, not crash."""
        import httpx as real_httpx
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Simulate connection error
            mock_response = AsyncMock()
            mock_response.__aenter__ = AsyncMock(
                side_effect=real_httpx.ConnectError("Connection refused")
            )
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=kalshi")
            assert resp.status_code == 200
            events = self._parse_sse_events(resp.text)
            error_events = [e for e in events if e[0] == "error"]
            assert len(error_events) > 0
            # Check error message mentions unavailability
            assert any("unavailable" in e[1].lower() or "connect" in e[1].lower()
                       for e in error_events)

    def test_ask_includes_sources_event(self, seeded_client):
        """After streaming completes, a sources event with citations is sent."""
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_response = AsyncMock()
            mock_response.status_code = 200

            async def mock_aiter():
                yield '{"response":"Hello","done":false}'
                yield '{"response":"","done":true}'

            mock_response.aiter_lines = mock_aiter
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=kalshi")
            assert resp.status_code == 200
            events = self._parse_sse_events(resp.text)
            # Should have a sources event
            source_events = [e for e in events if e[0] == "sources"]
            assert len(source_events) > 0
            # Sources should be valid JSON with facts and messages
            sources_data = json.loads(source_events[0][1])
            assert "facts" in sources_data
            assert "messages" in sources_data

    def test_ask_sources_contain_fact_ids(self, seeded_client):
        """Sources event facts contain IDs for linking to inspect view."""
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_response = AsyncMock()
            mock_response.status_code = 200

            async def mock_aiter():
                yield '{"response":"Test","done":false}'
                yield '{"response":"","done":true}'

            mock_response.aiter_lines = mock_aiter
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=kalshi")
            events = self._parse_sse_events(resp.text)
            source_events = [e for e in events if e[0] == "sources"]
            assert len(source_events) > 0
            sources_data = json.loads(source_events[0][1])
            # Facts should have id fields for linking
            if sources_data["facts"]:
                assert "id" in sources_data["facts"][0]

    def test_ask_sends_done_event(self, seeded_client):
        """Ask endpoint sends a done event when stream completes."""
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_response = AsyncMock()
            mock_response.status_code = 200

            async def mock_aiter():
                yield '{"response":"Test","done":false}'
                yield '{"response":"","done":true}'

            mock_response.aiter_lines = mock_aiter
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=kalshi")
            events = self._parse_sse_events(resp.text)
            done_events = [e for e in events if e[0] == "done"]
            assert len(done_events) > 0

    def test_ask_streams_token_events(self, seeded_client):
        """Ask endpoint sends individual token events during streaming."""
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_response = AsyncMock()
            mock_response.status_code = 200

            async def mock_aiter():
                yield '{"response":"Hello","done":false}'
                yield '{"response":" world","done":false}'
                yield '{"response":"","done":true}'

            mock_response.aiter_lines = mock_aiter
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=kalshi")
            events = self._parse_sse_events(resp.text)
            token_events = [e for e in events if e[0] == "token"]
            assert len(token_events) >= 2
            # First token should be "Hello"
            assert token_events[0][1] == "Hello"
            assert token_events[1][1] == " world"

    def test_ask_project_filter(self, seeded_client):
        """Ask endpoint accepts project filter."""
        with patch("src.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_response = AsyncMock()
            mock_response.status_code = 200

            async def mock_aiter():
                yield '{"response":"Test","done":false}'
                yield '{"response":"","done":true}'

            mock_response.aiter_lines = mock_aiter
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=mock_response)

            resp = seeded_client.get("/api/ask?q=deploy&project=kalshi")
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]


class TestAskFrontend:
    """Tests for Ask mode frontend integration."""

    def test_html_has_ask_mode_tab(self, client):
        """HTML page has Ask mode toggle tab."""
        resp = client.get("/")
        html = resp.text
        assert 'data-mode="ask"' in html
        assert "Ask" in html

    def test_js_handles_ask_mode(self, client):
        """app.js has code to handle Ask mode submission."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "/api/ask" in js
        assert "EventSource" in js or "getReader" in js or "event-stream" in js.lower()

    def test_js_renders_citations(self, client):
        """app.js has code to render source citations."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "sources" in js.lower() or "citation" in js.lower()

    def test_js_has_loading_indicator(self, client):
        """app.js shows a loading indicator for Ask mode."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "loading" in js.lower() or "waiting" in js.lower() or "Thinking" in js

    def test_js_citation_links_to_fact_inspect(self, client):
        """app.js creates clickable links for fact citations to inspect view."""
        resp = client.get("/static/app.js")
        js = resp.text
        # Should contain fact-id linking or showFactInspect for citations
        assert "showFactInspect" in js or "fact-id" in js or "data-fact-id" in js


class TestSearchTypeParam:
    """Tests for the search type parameter (FTS vs semantic)."""

    def test_html_has_semantic_checkbox(self, client):
        """HTML page has an 'Include semantic results' checkbox."""
        resp = client.get("/")
        html = resp.text
        assert 'id="include-semantic"' in html
        assert "semantic" in html.lower()

    def test_js_uses_type_param(self, client):
        """app.js uses the type parameter in search requests."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "type=" in js
        assert "include-semantic" in js


# --- Facts CRUD API Tests ---

class TestFactsListEndpoint:
    """Tests for GET /api/facts — paginated list with filters."""

    def test_list_facts_returns_structure(self, facts_client):
        """GET /api/facts returns {facts, total, offset, limit}."""
        resp = facts_client.get("/api/facts")
        assert resp.status_code == 200
        data = resp.json()
        assert "facts" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        assert isinstance(data["facts"], list)
        assert isinstance(data["total"], int)

    def test_list_facts_default_pagination(self, facts_client):
        """Default: offset=0, limit=50."""
        resp = facts_client.get("/api/facts")
        data = resp.json()
        assert data["offset"] == 0
        assert data["limit"] == 50
        assert data["total"] == 15

    def test_list_facts_custom_pagination(self, facts_client):
        """Custom offset/limit narrows results."""
        resp = facts_client.get("/api/facts?offset=5&limit=3")
        data = resp.json()
        assert len(data["facts"]) == 3
        assert data["offset"] == 5
        assert data["limit"] == 3
        assert data["total"] == 15

    def test_list_facts_offset_beyond_total(self, facts_client):
        """Offset beyond total returns empty list but correct total."""
        resp = facts_client.get("/api/facts?offset=100")
        data = resp.json()
        assert data["facts"] == []
        assert data["total"] == 15

    def test_list_facts_fact_structure(self, facts_client):
        """Each fact has expected fields."""
        resp = facts_client.get("/api/facts?limit=1")
        data = resp.json()
        assert len(data["facts"]) == 1
        fact = data["facts"][0]
        assert "id" in fact
        assert "fact" in fact
        assert "category" in fact
        assert "confidence" in fact
        assert "project" in fact
        assert "timestamp" in fact
        assert "compressed_details" in fact

    def test_list_facts_filter_by_category(self, facts_client):
        """Category filter narrows results."""
        resp = facts_client.get("/api/facts?category=tool")
        data = resp.json()
        assert data["total"] > 0
        for f in data["facts"]:
            assert f["category"] == "tool"

    def test_list_facts_filter_by_project(self, facts_client):
        """Project filter narrows results."""
        resp = facts_client.get("/api/facts?project=kalshi")
        data = resp.json()
        assert data["total"] > 0
        for f in data["facts"]:
            assert "kalshi" in (f["project"] or "").lower()

    def test_list_facts_filter_by_min_confidence(self, facts_client):
        """min_confidence filter works."""
        resp = facts_client.get("/api/facts?min_confidence=0.8")
        data = resp.json()
        for f in data["facts"]:
            assert f["confidence"] >= 0.8

    def test_list_facts_filter_by_max_confidence(self, facts_client):
        """max_confidence filter works."""
        resp = facts_client.get("/api/facts?max_confidence=0.5")
        data = resp.json()
        for f in data["facts"]:
            assert f["confidence"] <= 0.5

    def test_list_facts_filter_confidence_range(self, facts_client):
        """Combined min/max confidence filters work."""
        resp = facts_client.get("/api/facts?min_confidence=0.3&max_confidence=0.7")
        data = resp.json()
        for f in data["facts"]:
            assert 0.3 <= f["confidence"] <= 0.7

    def test_list_facts_sort_by_confidence(self, facts_client):
        """Sort by confidence ascending."""
        resp = facts_client.get("/api/facts?sort=confidence&order=asc")
        data = resp.json()
        confs = [f["confidence"] for f in data["facts"]]
        assert confs == sorted(confs)

    def test_list_facts_sort_by_confidence_desc(self, facts_client):
        """Sort by confidence descending."""
        resp = facts_client.get("/api/facts?sort=confidence&order=desc")
        data = resp.json()
        confs = [f["confidence"] for f in data["facts"]]
        assert confs == sorted(confs, reverse=True)

    def test_list_facts_sort_by_category(self, facts_client):
        """Sort by category."""
        resp = facts_client.get("/api/facts?sort=category&order=asc")
        data = resp.json()
        cats = [f["category"] for f in data["facts"]]
        assert cats == sorted(cats)

    def test_list_facts_default_sort_timestamp_desc(self, facts_client):
        """Default sort is timestamp DESC."""
        resp = facts_client.get("/api/facts")
        data = resp.json()
        timestamps = [f["timestamp"] for f in data["facts"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_list_facts_returns_json(self, facts_client):
        """Endpoint returns JSON content type."""
        resp = facts_client.get("/api/facts")
        assert "application/json" in resp.headers["content-type"]


class TestFactUpdateEndpoint:
    """Tests for PUT /api/facts/{id} — update fact text/confidence."""

    def test_update_fact_text(self, facts_client):
        """Update fact text via PUT."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"fact": "Updated fact text"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fact"] == "Updated fact text"
        assert data["id"] == 1

    def test_update_fact_confidence(self, facts_client):
        """Update fact confidence via PUT."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"confidence": 0.75}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 0.75

    def test_update_fact_both_fields(self, facts_client):
        """Update both fact text and confidence."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"fact": "New text", "confidence": 0.5}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fact"] == "New text"
        assert data["confidence"] == 0.5

    def test_update_fact_clamps_high_confidence(self, facts_client):
        """Confidence > 1.0 is clamped to 1.0."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"confidence": 2.0}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 1.0

    def test_update_fact_clamps_low_confidence(self, facts_client):
        """Confidence < 0.0 is clamped to 0.0."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"confidence": -0.5}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 0.0

    def test_update_fact_clamps_zero(self, facts_client):
        """Confidence 0.0 is valid and kept."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"confidence": 0.0}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 0.0

    def test_update_fact_clamps_one(self, facts_client):
        """Confidence 1.0 is valid and kept."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"confidence": 1.0}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 1.0

    def test_update_fact_not_found(self, facts_client):
        """PUT non-existent fact returns 404."""
        resp = facts_client.put(
            "/api/facts/999999",
            json={"fact": "Updated text"}
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data

    def test_update_fact_persists(self, facts_client):
        """Updated fact is persisted (verify via GET inspect)."""
        facts_client.put("/api/facts/1", json={"fact": "Persisted update"})
        resp = facts_client.get("/api/facts/1")
        data = resp.json()
        assert data["fact"] == "Persisted update"

    def test_update_empty_body(self, facts_client):
        """PUT with empty body returns the fact unchanged (no error)."""
        resp = facts_client.put("/api/facts/1", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1

    def test_update_returns_full_fact(self, facts_client):
        """PUT returns the full fact object with all fields."""
        resp = facts_client.put(
            "/api/facts/1",
            json={"fact": "Full return test"}
        )
        data = resp.json()
        assert "id" in data
        assert "fact" in data
        assert "category" in data
        assert "confidence" in data
        assert "project" in data
        assert "timestamp" in data


class TestFactDeleteEndpoint:
    """Tests for DELETE /api/facts/{id} — delete a fact."""

    def test_delete_fact(self, facts_client):
        """DELETE returns {deleted: true}."""
        resp = facts_client.delete("/api/facts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True

    def test_delete_fact_removes_it(self, facts_client):
        """Deleted fact is gone from DB."""
        facts_client.delete("/api/facts/1")
        resp = facts_client.get("/api/facts/1")
        assert resp.status_code == 404

    def test_delete_fact_not_found(self, facts_client):
        """DELETE non-existent fact returns 404."""
        resp = facts_client.delete("/api/facts/999999")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data

    def test_delete_reduces_total(self, facts_client):
        """After deleting a fact, total count decreases."""
        resp = facts_client.get("/api/facts")
        original_total = resp.json()["total"]

        facts_client.delete("/api/facts/1")

        resp = facts_client.get("/api/facts")
        new_total = resp.json()["total"]
        assert new_total == original_total - 1

    def test_delete_fact_not_in_list(self, facts_client):
        """Deleted fact does not appear in list."""
        facts_client.delete("/api/facts/1")
        resp = facts_client.get("/api/facts?limit=50")
        data = resp.json()
        ids = [f["id"] for f in data["facts"]]
        assert 1 not in ids


class TestFactsCRUDIntegration:
    """Integration tests: edit reflected in search, etc."""

    def test_edit_reflected_in_search(self, seeded_client):
        """Editing a fact's text is reflected when searching for it."""
        # First update the fact
        resp = seeded_client.put(
            "/api/facts/1",
            json={"fact": "Kalshi uses kubernetes for deployment"}
        )
        assert resp.status_code == 200

        # Now search should find the updated text
        resp = seeded_client.get("/api/search?q=kubernetes")
        data = resp.json()
        found = any("kubernetes" in f.get("fact", "").lower() for f in data["facts"])
        assert found

    def test_delete_removes_from_search(self, seeded_client):
        """Deleting a fact removes it from search results."""
        # Delete fact 3
        resp = seeded_client.delete("/api/facts/3")
        assert resp.status_code == 200

        # Search for it — it should be gone
        resp = seeded_client.get("/api/search?q=SQLite+FTS5")
        data = resp.json()
        fact_ids = [f["id"] for f in data["facts"]]
        assert 3 not in fact_ids


class TestFactsFrontend:
    """Tests for Facts page frontend elements."""

    def test_html_has_facts_section(self, client):
        """HTML has a facts section."""
        resp = client.get("/")
        html = resp.text
        assert 'id="section-facts"' in html

    def test_html_has_facts_filters(self, client):
        """HTML has filter controls for category, project, confidence."""
        resp = client.get("/")
        html = resp.text
        assert "category" in html.lower()
        assert "confidence" in html.lower()

    def test_js_has_facts_crud_functions(self, client):
        """app.js has functions for loading, editing, deleting facts."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "loadFacts" in js or "fetchFacts" in js or "/api/facts" in js
        assert "DELETE" in js
        assert "PUT" in js

    def test_js_has_pagination(self, client):
        """app.js has pagination controls."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "offset" in js
        assert "limit" in js

    def test_js_escapes_fact_content(self, client):
        """app.js uses escapeHtml for fact content rendering."""
        resp = client.get("/static/app.js")
        js = resp.text
        # Should use escapeHtml extensively in facts rendering
        assert "escapeHtml" in js


# --- CLAUDE.md Editor API Tests ---

class TestClaudeMdGetEndpoint:
    """Tests for GET /api/claude-md — read CLAUDE.md file."""

    def test_get_claude_md_returns_content(self, client, tmp_path):
        """GET /api/claude-md returns file content when file exists."""
        test_file = tmp_path / "CLAUDE.md"
        test_file.write_text("# Test Content\nHello world")
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.get("/api/claude-md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "# Test Content\nHello world"
        assert "path" in data
        assert data.get("exists") is not False  # exists should be True or not present

    def test_get_claude_md_missing_file(self, client, tmp_path):
        """GET /api/claude-md returns empty content when file doesn't exist."""
        missing_file = tmp_path / "nonexistent" / "CLAUDE.md"
        with patch("src.web.CLAUDE_MD_PATH", missing_file):
            resp = client.get("/api/claude-md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == ""
        assert data["exists"] is False
        assert "path" in data

    def test_get_claude_md_returns_json(self, client, tmp_path):
        """GET /api/claude-md returns JSON content type."""
        test_file = tmp_path / "CLAUDE.md"
        test_file.write_text("test")
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.get("/api/claude-md")
        assert "application/json" in resp.headers["content-type"]

    def test_get_claude_md_has_path_field(self, client, tmp_path):
        """Response includes the path field."""
        test_file = tmp_path / "CLAUDE.md"
        test_file.write_text("test")
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.get("/api/claude-md")
        data = resp.json()
        assert "path" in data
        assert len(data["path"]) > 0

    def test_get_claude_md_empty_file(self, client, tmp_path):
        """GET /api/claude-md handles empty file."""
        test_file = tmp_path / "CLAUDE.md"
        test_file.write_text("")
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.get("/api/claude-md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == ""


class TestClaudeMdPutEndpoint:
    """Tests for PUT /api/claude-md — save CLAUDE.md file."""

    def test_put_claude_md_saves_content(self, client, tmp_path):
        """PUT /api/claude-md saves content to file."""
        test_file = tmp_path / "CLAUDE.md"
        test_file.write_text("original")
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.put("/api/claude-md", json={"content": "# Updated\nNew content"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["bytes"] > 0
        # Verify file was actually written
        assert test_file.read_text() == "# Updated\nNew content"

    def test_put_claude_md_creates_file(self, client, tmp_path):
        """PUT /api/claude-md creates file if it doesn't exist."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "CLAUDE.md"
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.put("/api/claude-md", json={"content": "New file content"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert test_file.exists()
        assert test_file.read_text() == "New file content"

    def test_put_claude_md_returns_byte_count(self, client, tmp_path):
        """PUT /api/claude-md returns correct byte count."""
        test_file = tmp_path / "CLAUDE.md"
        content = "Hello, world!"
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.put("/api/claude-md", json={"content": content})
        data = resp.json()
        assert data["bytes"] == len(content.encode("utf-8"))

    def test_put_claude_md_empty_content(self, client, tmp_path):
        """PUT /api/claude-md with empty content saves empty file."""
        test_file = tmp_path / "CLAUDE.md"
        test_file.write_text("something")
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.put("/api/claude-md", json={"content": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["bytes"] == 0
        assert test_file.read_text() == ""

    def test_put_claude_md_roundtrip(self, client, tmp_path):
        """Content survives a PUT then GET round-trip."""
        test_file = tmp_path / "CLAUDE.md"
        content = "# Round Trip\n\nTest content with **bold** and `code`"
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            client.put("/api/claude-md", json={"content": content})
            resp = client.get("/api/claude-md")
        data = resp.json()
        assert data["content"] == content

    def test_put_claude_md_creates_parent_dirs(self, client, tmp_path):
        """PUT /api/claude-md creates parent directories if needed."""
        test_file = tmp_path / "deep" / "nested" / "CLAUDE.md"
        with patch("src.web.CLAUDE_MD_PATH", test_file):
            resp = client.put("/api/claude-md", json={"content": "nested content"})
        assert resp.status_code == 200
        assert test_file.exists()


# --- Context Preview API Tests ---

class TestContextPreviewEndpoint:
    """Tests for GET /api/context-preview — run inject.py and return output."""

    def test_context_preview_returns_content(self, client):
        """GET /api/context-preview returns content and tokens_estimate."""
        mock_output = "# Memory Context\n\n## Key Facts\n- Some fact"
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr="",
            )
            resp = client.get("/api/context-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == mock_output
        assert "tokens_estimate" in data
        assert isinstance(data["tokens_estimate"], int)

    def test_context_preview_default_max_tokens(self, client):
        """Default max_tokens is 2000."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test", stderr="")
            client.get("/api/context-preview")
            # Check that --max-tokens 2000 was passed
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            assert "--max-tokens" in cmd
            idx = cmd.index("--max-tokens")
            assert cmd[idx + 1] == "2000"

    def test_context_preview_custom_max_tokens(self, client):
        """Custom max_tokens parameter is passed to inject.py."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test", stderr="")
            client.get("/api/context-preview?max_tokens=500")
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            assert "--max-tokens" in cmd
            idx = cmd.index("--max-tokens")
            assert cmd[idx + 1] == "500"

    def test_context_preview_with_project(self, client):
        """Project parameter is passed to inject.py."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test", stderr="")
            client.get("/api/context-preview?project=kalshi")
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            assert "--project" in cmd
            idx = cmd.index("--project")
            assert cmd[idx + 1] == "kalshi"

    def test_context_preview_no_project_omits_flag(self, client):
        """When no project is provided, --project flag is not passed."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test", stderr="")
            client.get("/api/context-preview")
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            assert "--project" not in cmd

    def test_context_preview_subprocess_error(self, client):
        """Subprocess failure returns error gracefully."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="some error",
            )
            resp = client.get("/api/context-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data or "content" in data

    def test_context_preview_subprocess_exception(self, client):
        """Exception during subprocess call returns error gracefully."""
        with patch("src.web.subprocess.run", side_effect=Exception("subprocess failed")):
            resp = client.get("/api/context-preview")
        # Should not crash - return error or 500
        assert resp.status_code in (200, 500)
        data = resp.json()
        assert "error" in data

    def test_context_preview_tokens_estimate_reasonable(self, client):
        """Token estimate is approximately len(content)//4."""
        mock_output = "A" * 400  # ~100 tokens
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            resp = client.get("/api/context-preview")
        data = resp.json()
        assert data["tokens_estimate"] == 100  # 400 chars // 4

    def test_context_preview_returns_json(self, client):
        """Context preview returns JSON content type."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test", stderr="")
            resp = client.get("/api/context-preview")
        assert "application/json" in resp.headers["content-type"]

    def test_context_preview_uses_no_detect(self, client):
        """Context preview passes --no-detect to avoid PWD auto-detection."""
        with patch("src.web.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test", stderr="")
            client.get("/api/context-preview")
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            assert "--no-detect" in cmd


# --- CLAUDE.md Editor Frontend Tests ---

class TestClaudeMdFrontend:
    """Tests for CLAUDE.md editor frontend elements."""

    def test_html_has_claude_md_section(self, client):
        """HTML has a CLAUDE.md editor section."""
        resp = client.get("/")
        html = resp.text
        assert 'id="section-claude-md"' in html

    def test_html_has_editor_textarea(self, client):
        """HTML has an editor textarea for CLAUDE.md."""
        resp = client.get("/")
        html = resp.text
        assert 'id="claude-md-editor"' in html or 'claude-md-editor' in html

    def test_html_has_preview_area(self, client):
        """HTML has a preview area for rendered markdown."""
        resp = client.get("/")
        html = resp.text
        assert 'id="claude-md-preview"' in html or 'claude-md-preview' in html

    def test_html_has_save_button(self, client):
        """HTML has a save button for CLAUDE.md."""
        resp = client.get("/")
        html = resp.text
        assert 'id="claude-md-save"' in html or 'claude-md-save' in html

    def test_js_has_claude_md_functions(self, client):
        """app.js has functions for loading and saving CLAUDE.md."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "/api/claude-md" in js

    def test_js_has_markdown_rendering(self, client):
        """app.js has simple markdown rendering function."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "renderMarkdown" in js or "markdownToHtml" in js or "renderMd" in js

    def test_js_has_debounced_preview(self, client):
        """app.js debounces preview updates."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "debounce" in js.lower() or "setTimeout" in js


# --- Context Preview Frontend Tests ---

class TestContextPreviewFrontend:
    """Tests for Memory Context Preview frontend elements."""

    def test_html_has_context_preview_section(self, client):
        """HTML has context preview section."""
        resp = client.get("/")
        html = resp.text
        assert 'id="section-context-preview"' in html

    def test_html_has_token_slider(self, client):
        """HTML has a token budget slider."""
        resp = client.get("/")
        html = resp.text
        assert 'id="token-budget-slider"' in html or 'token-budget' in html

    def test_html_has_regenerate_button(self, client):
        """HTML has a regenerate button."""
        resp = client.get("/")
        html = resp.text
        assert "Regenerate" in html or "regenerate" in html

    def test_html_has_preview_area(self, client):
        """HTML has context preview display area."""
        resp = client.get("/")
        html = resp.text
        assert 'id="context-preview-output"' in html or 'context-preview-output' in html

    def test_js_has_context_preview_functions(self, client):
        """app.js has functions for loading context preview."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "/api/context-preview" in js

    def test_js_has_project_dropdown(self, client):
        """app.js handles project dropdown for context preview."""
        resp = client.get("/static/app.js")
        js = resp.text
        assert "context-project" in js or "contextProject" in js

    def test_html_has_project_filter(self, client):
        """HTML has a project filter for context preview."""
        resp = client.get("/")
        html = resp.text
        assert 'id="context-project-filter"' in html or 'context-project' in html


# --- Projects List API Test ---

class TestProjectsEndpoint:
    """Tests for GET /api/projects — list distinct projects."""

    def test_projects_returns_list(self, seeded_client):
        """GET /api/projects returns a list of distinct project names."""
        resp = seeded_client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert isinstance(data["projects"], list)
        assert len(data["projects"]) > 0

    def test_projects_distinct(self, seeded_client):
        """Projects list has no duplicates."""
        resp = seeded_client.get("/api/projects")
        data = resp.json()
        projects = data["projects"]
        assert len(projects) == len(set(projects))
