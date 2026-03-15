"""FastAPI web application for Claude Memory search and curation UI.

Entry point: uvicorn src.web:app --host 0.0.0.0 --port 8585
"""

import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src import memory_db

# LLM settings
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.3:70b"

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
CLAUDE_MD_PATH = Path.home() / ".claude" / "CLAUDE.md"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
INJECT_SCRIPT = PROJECT_ROOT / "src" / "inject.py"

app = FastAPI(title="Claude Memory", docs_url=None, redoc_url=None)


# --- Error handlers ---

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Return JSON for 404 errors instead of HTML."""
    return JSONResponse(
        status_code=404,
        content={"error": "Not found"},
    )


@app.exception_handler(422)
async def validation_error_handler(request: Request, exc):
    """Return JSON for 422 validation errors instead of HTML."""
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "detail": str(exc)},
    )


# --- Helper functions ---

def _truncate(text, max_len=400):
    """Truncate text with ellipsis."""
    if not text:
        return ""
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _search_sessions_fts(conn, query, project=None, limit=10):
    """Search sessions via FTS5, grouping messages by session_id."""
    sql = """
        SELECT m.session_id, m.project,
               MIN(m.timestamp) as first_msg,
               MAX(m.timestamp) as last_msg,
               COUNT(*) as msg_count,
               GROUP_CONCAT(SUBSTR(m.content, 1, 80), ' | ') as snippets
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH ?
    """
    params = [query]

    if project:
        sql += " AND m.project LIKE ?"
        params.append(f"%{project}%")

    sql += " GROUP BY m.session_id ORDER BY last_msg DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _semantic_search(query, conn, project=None, limit=5):
    """Attempt semantic search via embed.py. Returns list or empty list on failure."""
    try:
        from src.embed import search_similar
        results = search_similar(query, conn=conn, top_k=limit,
                                 project=project, decay_halflife_days=30)
        # Serialize results — drop non-JSON-serializable fields
        out = []
        for r in (results or []):
            out.append({
                "id": r.get("id"),
                "session_id": r.get("session_id"),
                "project": r.get("project"),
                "role": r.get("role"),
                "content": _truncate(r.get("content", ""), 400),
                "timestamp": r.get("timestamp"),
                "score": round(r.get("score", 0), 4),
            })
        return out
    except Exception:
        return []


# --- Routes ---

@app.get("/")
async def serve_index():
    """Serve the main HTML page."""
    return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")


@app.get("/api/health")
async def health_check():
    """Health check endpoint. Tests database connectivity."""
    db_accessible = False
    try:
        conn = memory_db.get_conn()
        conn.execute("SELECT 1")
        conn.close()
        db_accessible = True
    except Exception:
        pass

    return {"status": "ok", "db_accessible": db_accessible}


@app.get("/api/search")
async def search(
    q: str = Query("", description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    project: Optional[str] = Query(None, description="Filter by project"),
    type: Optional[str] = Query(None, description="Search type: fts (default), all, or semantic"),
):
    """Combined search: FTS messages + facts + sessions + optional semantic.

    The `type` query parameter controls whether semantic (FAISS) search is included:
    - type=fts (or omitted): FTS-only search — fast (<500ms).
    - type=all or type=semantic: includes FAISS semantic search results too.
    """
    start = time.time()

    if not q.strip():
        return {
            "messages": [],
            "facts": [],
            "sessions": [],
            "semantic": [],
            "timing_ms": 0,
            "query": "",
        }

    query = q.strip()
    include_semantic = type in ("all", "semantic")
    conn = memory_db.get_conn()

    # FTS message search
    messages = []
    try:
        rows = memory_db.search_fts(conn, query, project=project, limit=limit)
        for r in rows:
            messages.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "project": r.get("project"),
                "role": r["role"],
                "content": _truncate(r["content"], 400),
                "timestamp": r.get("timestamp"),
            })
    except sqlite3.OperationalError:
        # Invalid FTS5 syntax — return empty messages gracefully
        pass

    # FTS fact search
    facts = []
    try:
        rows = memory_db.search_facts_fts(conn, query, project=project, limit=limit)
        for r in rows:
            facts.append({
                "id": r["id"],
                "fact": r["fact"],
                "category": r.get("category"),
                "confidence": r.get("confidence"),
                "project": r.get("project"),
                "timestamp": r.get("timestamp"),
                "compressed_details": r.get("compressed_details"),
            })
    except sqlite3.OperationalError:
        pass

    # Session search via FTS
    sessions = []
    try:
        rows = _search_sessions_fts(conn, query, project=project, limit=min(limit, 10))
        for r in rows:
            sessions.append({
                "session_id": r["session_id"],
                "project": r.get("project"),
                "first_msg": r.get("first_msg"),
                "last_msg": r.get("last_msg"),
                "msg_count": r.get("msg_count"),
                "snippets": _truncate(r.get("snippets", ""), 200),
            })
    except sqlite3.OperationalError:
        pass

    # Semantic search — only when explicitly requested via type=all or type=semantic
    semantic = []
    if include_semantic:
        semantic = _semantic_search(query, conn, project=project, limit=min(limit, 5))

    conn.close()
    elapsed = round((time.time() - start) * 1000, 1)

    return {
        "messages": messages,
        "facts": facts,
        "sessions": sessions,
        "semantic": semantic,
        "timing_ms": elapsed,
        "query": query,
    }


def _gather_ask_context(query, project=None):
    """Gather facts and messages for LLM synthesis (similar to memory_deep_recall)."""
    conn = memory_db.get_conn()

    # Search facts via FTS
    facts = []
    try:
        rows = memory_db.search_facts_fts(conn, query, project=project, limit=5)
        for r in rows:
            facts.append({
                "id": r["id"],
                "fact": r["fact"],
                "category": r.get("category"),
                "confidence": r.get("confidence"),
                "compressed_details": r.get("compressed_details"),
            })
    except sqlite3.OperationalError:
        pass

    # Search messages via FTS
    messages = []
    try:
        rows = memory_db.search_fts(conn, query, project=project, limit=10)
        for r in rows:
            messages.append({
                "id": r["id"],
                "session_id": r.get("session_id"),
                "project": r.get("project"),
                "role": r.get("role"),
                "content": _truncate(r.get("content", ""), 600),
                "timestamp": r.get("timestamp"),
            })
    except sqlite3.OperationalError:
        pass

    conn.close()
    return facts, messages


def _build_synthesis_prompt(query, facts, messages):
    """Build the LLM prompt from gathered context."""
    context_parts = []

    if facts:
        fact_lines = []
        for f in facts:
            compressed = ""
            cd = f.get("compressed_details")
            if cd and cd.strip() and cd.strip() != "none":
                compressed = f" (compressed: {cd})"
            fact_lines.append(f"- [{f.get('category', '?')}] {f['fact']}{compressed}")
        context_parts.append("EXTRACTED FACTS:\n" + "\n".join(fact_lines))

    if messages:
        msg_lines = []
        for m in messages[:8]:
            ts = (m.get("timestamp") or "")[:16]
            proj = m.get("project") or "no-project"
            msg_lines.append(f"[{ts}] {proj} ({m.get('role', '?')}): {m['content']}")
        context_parts.append("SOURCE MESSAGES:\n" + "\n".join(msg_lines))

    context = "\n\n".join(context_parts)

    return (
        f'Based on the following memory context, provide a concise, accurate '
        f'answer to this question: "{query}"\n\n'
        f'{context}\n\n'
        f'Rules:\n'
        f'- Only state things supported by the context above\n'
        f'- If the context is insufficient, say what you found and what\'s missing\n'
        f'- Be concise — this answer will be used as context in another conversation\n'
        f'- Include specific details (file paths, commands, config values) when available'
    )


@app.get("/api/ask")
async def ask(
    q: str = Query("", description="Ask query"),
    project: Optional[str] = Query(None, description="Filter by project"),
):
    """Ask mode: streaming LLM synthesis via ollama with SSE."""

    async def event_stream():
        query = q.strip()
        if not query:
            yield "event: error\ndata: Please enter a question.\n\n"
            return

        # Gather context from memory
        facts, messages = _gather_ask_context(query, project=project)

        if not facts and not messages:
            yield "event: error\ndata: No relevant memories found for your question.\n\n"
            return

        # Build the synthesis prompt
        prompt = _build_synthesis_prompt(query, facts, messages)

        # Stream from ollama
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": True,
                    },
                ) as response:
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("response", "")
                            if token:
                                yield f"event: token\ndata: {token}\n\n"
                            if chunk.get("done", False):
                                break
                        except json.JSONDecodeError:
                            continue
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException, OSError) as exc:
            yield f"event: error\ndata: LLM service unavailable. Could not connect to ollama. ({type(exc).__name__})\n\n"
            return
        except Exception as exc:
            yield f"event: error\ndata: LLM error: {type(exc).__name__}\n\n"
            return

        # Send sources as a final event
        sources_data = json.dumps({
            "facts": facts,
            "messages": [
                {
                    "id": m["id"],
                    "project": m.get("project"),
                    "role": m.get("role"),
                    "content": _truncate(m.get("content", ""), 200),
                    "timestamp": m.get("timestamp"),
                }
                for m in messages[:5]
            ],
        })
        yield f"event: sources\ndata: {sources_data}\n\n"
        yield "event: done\ndata: \n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/facts")
async def list_facts(
    category: Optional[str] = Query(None, description="Filter by category"),
    project: Optional[str] = Query(None, description="Filter by project"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum confidence"),
    max_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Maximum confidence"),
    sort: str = Query("timestamp", description="Sort field: timestamp, confidence, category"),
    order: str = Query("desc", description="Sort order: asc or desc"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
):
    """List facts with filters, sorting, and pagination."""
    conn = memory_db.get_conn()

    # Build query with filters
    where_clauses = ["1=1"]
    params = []

    if category:
        where_clauses.append("f.category = ?")
        params.append(category)
    if project:
        where_clauses.append("f.project LIKE ?")
        params.append(f"%{project}%")
    if min_confidence is not None:
        where_clauses.append("f.confidence >= ?")
        params.append(min_confidence)
    if max_confidence is not None:
        where_clauses.append("f.confidence <= ?")
        params.append(max_confidence)

    where_sql = " AND ".join(where_clauses)

    # Count total matching facts
    count_sql = f"SELECT COUNT(*) FROM facts f WHERE {where_sql}"
    total = conn.execute(count_sql, params).fetchone()[0]

    # Validate sort field
    allowed_sorts = {"timestamp": "f.timestamp", "confidence": "f.confidence", "category": "f.category"}
    sort_col = allowed_sorts.get(sort, "f.timestamp")
    sort_order = "ASC" if order.lower() == "asc" else "DESC"

    # Query with pagination
    data_sql = f"""
        SELECT f.id, f.fact, f.category, f.confidence, f.project,
               f.timestamp, f.compressed_details
        FROM facts f
        WHERE {where_sql}
        ORDER BY {sort_col} {sort_order}
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(data_sql, params + [limit, offset]).fetchall()

    facts = []
    for r in rows:
        cd = None
        try:
            raw_cd = r["compressed_details"]
            if raw_cd and raw_cd.strip() and raw_cd.strip() != "none":
                cd = raw_cd
        except (IndexError, KeyError):
            pass

        facts.append({
            "id": r["id"],
            "fact": r["fact"],
            "category": r["category"],
            "confidence": r["confidence"],
            "project": r["project"],
            "timestamp": r["timestamp"],
            "compressed_details": cd,
        })

    conn.close()
    return {"facts": facts, "total": total, "offset": offset, "limit": limit}


@app.put("/api/facts/{fact_id}")
async def update_fact(fact_id: int, request: Request):
    """Update a fact's text and/or confidence. Confidence is clamped to [0.0, 1.0]."""
    body = await request.json()
    conn = memory_db.get_conn()

    # Check fact exists
    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if not fact:
        conn.close()
        return JSONResponse(
            status_code=404,
            content={"error": f"Fact {fact_id} not found"},
        )

    # Build update fields
    updates = []
    params = []

    if "fact" in body:
        updates.append("fact = ?")
        params.append(body["fact"])

    if "confidence" in body:
        # Clamp confidence to [0.0, 1.0]
        conf = max(0.0, min(1.0, float(body["confidence"])))
        updates.append("confidence = ?")
        params.append(conf)

    if updates:
        sql = f"UPDATE facts SET {', '.join(updates)} WHERE id = ?"
        params.append(fact_id)
        conn.execute(sql, params)
        conn.commit()

    # Return the updated fact
    updated = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    cd = None
    try:
        raw_cd = updated["compressed_details"]
        if raw_cd and raw_cd.strip() and raw_cd.strip() != "none":
            cd = raw_cd
    except (IndexError, KeyError):
        pass

    result = {
        "id": updated["id"],
        "fact": updated["fact"],
        "category": updated["category"],
        "confidence": updated["confidence"],
        "project": updated["project"],
        "timestamp": updated["timestamp"],
        "compressed_details": cd,
    }
    conn.close()
    return result


@app.delete("/api/facts/{fact_id}")
async def delete_fact(fact_id: int):
    """Delete a fact by ID."""
    conn = memory_db.get_conn()

    # Check fact exists
    fact = conn.execute("SELECT id FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if not fact:
        conn.close()
        return JSONResponse(
            status_code=404,
            content={"error": f"Fact {fact_id} not found"},
        )

    conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    conn.commit()
    conn.close()
    return {"deleted": True}


@app.get("/api/facts/{fact_id}")
async def get_fact(fact_id: int):
    """Fact inspect endpoint — returns fact details, source message, siblings, entities."""
    conn = memory_db.get_conn()

    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if not fact:
        conn.close()
        return JSONResponse(
            status_code=404,
            content={"error": f"Fact {fact_id} not found"},
        )

    result = {
        "id": fact["id"],
        "fact": fact["fact"],
        "category": fact["category"],
        "confidence": fact["confidence"],
        "project": fact["project"],
        "session_id": fact["session_id"],
        "source_message_id": fact["source_message_id"],
        "timestamp": fact["timestamp"],
        "compressed_details": None,
    }

    # Handle compressed_details (may be missing column in old schemas)
    try:
        cd = fact["compressed_details"]
        if cd and cd.strip() and cd.strip() != "none":
            result["compressed_details"] = cd
    except (IndexError, KeyError):
        pass

    # Get source message
    source_message = None
    session_id = fact["session_id"]
    if fact["source_message_id"]:
        msg = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (fact["source_message_id"],)
        ).fetchone()
        if msg:
            source_message = {
                "id": msg["id"],
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": msg["timestamp"],
                "project": msg["project"],
                "session_id": msg["session_id"],
            }
            session_id = msg["session_id"] or session_id
    result["source_message"] = source_message

    # Get sibling facts from the same session
    siblings = []
    if session_id:
        rows = conn.execute(
            "SELECT id, fact, category, confidence FROM facts "
            "WHERE session_id = ? AND id != ? AND confidence > 0 ORDER BY id",
            (session_id, fact_id),
        ).fetchall()
        for s in rows[:10]:
            siblings.append({
                "id": s["id"],
                "fact": s["fact"],
                "category": s["category"],
                "confidence": s["confidence"],
            })
    result["siblings"] = siblings

    # Get related entities from the same session
    entities = []
    if session_id:
        rows = conn.execute("""
            SELECT DISTINCT e.name, e.entity_type, e.mention_count
            FROM entity_mentions em
            JOIN entities e ON e.id = em.entity_id
            WHERE em.session_id = ? AND e.id > 0
            ORDER BY e.mention_count DESC LIMIT 10
        """, (session_id,)).fetchall()
        for e in rows:
            entities.append({
                "name": e["name"],
                "entity_type": e["entity_type"],
                "mention_count": e["mention_count"],
            })
    result["entities"] = entities

    conn.close()
    return result


@app.get("/api/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    project: Optional[str] = Query(None, description="Filter by project"),
):
    """List recent sessions with summary info."""
    conn = memory_db.get_conn()
    rows = memory_db.list_recent_sessions(conn, project=project, limit=limit)
    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r["session_id"],
            "project": r.get("project"),
            "first_msg": r.get("first_msg"),
            "last_msg": r.get("last_msg"),
            "msg_count": r.get("msg_count"),
            "snippets": _truncate(r.get("snippets", ""), 200),
        })
    conn.close()
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def get_session(
    session_id: str,
    limit: int = Query(100, ge=1, le=1000),
):
    """Get session messages in chronological order."""
    conn = memory_db.get_conn()
    rows = memory_db.get_session_messages(conn, session_id, limit=limit)
    if not rows:
        conn.close()
        return JSONResponse(
            status_code=404,
            content={"error": f"Session {session_id} not found"},
        )
    messages = []
    for r in rows:
        messages.append({
            "id": r["id"],
            "session_id": r["session_id"],
            "project": r.get("project"),
            "role": r["role"],
            "content": r["content"],
            "timestamp": r.get("timestamp"),
        })
    conn.close()
    return {
        "session_id": rows[0]["session_id"],
        "project": rows[0].get("project"),
        "messages": messages,
    }


# --- CLAUDE.md Editor Endpoints ---

@app.get("/api/claude-md")
async def get_claude_md():
    """Read ~/.claude/CLAUDE.md and return its content."""
    path = CLAUDE_MD_PATH
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            return {"content": content, "path": str(path), "exists": True}
        else:
            return {"content": "", "path": str(path), "exists": False}
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read CLAUDE.md: {type(exc).__name__}"},
        )


@app.put("/api/claude-md")
async def put_claude_md(request: Request):
    """Save content to ~/.claude/CLAUDE.md."""
    body = await request.json()
    content = body.get("content", "")
    path = CLAUDE_MD_PATH

    try:
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        path.write_bytes(encoded)
        return {"saved": True, "bytes": len(encoded)}
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to save CLAUDE.md: {type(exc).__name__}"},
        )


# --- Context Preview Endpoint ---

@app.get("/api/context-preview")
async def context_preview(
    max_tokens: int = Query(2000, ge=100, le=10000, description="Token budget"),
    project: Optional[str] = Query(None, description="Filter by project"),
):
    """Run inject.py --stdout and return the generated context preview."""
    try:
        cmd = [
            str(VENV_PYTHON),
            str(INJECT_SCRIPT),
            "--stdout",
            "--no-detect",
            "--max-tokens",
            str(max_tokens),
        ]

        if project:
            cmd.extend(["--project", project])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return JSONResponse(
                status_code=200,
                content={
                    "content": "",
                    "tokens_estimate": 0,
                    "error": f"inject.py failed: {result.stderr.strip() or 'unknown error'}",
                },
            )

        content = result.stdout
        tokens_estimate = len(content) // 4

        return {"content": content, "tokens_estimate": tokens_estimate}

    except subprocess.TimeoutExpired:
        return JSONResponse(
            status_code=200,
            content={
                "content": "",
                "tokens_estimate": 0,
                "error": "inject.py timed out",
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Context preview failed: {type(exc).__name__}"},
        )


# --- Projects List Endpoint ---

@app.get("/api/projects")
async def list_projects():
    """Return distinct project names from facts and messages."""
    conn = memory_db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT project FROM facts WHERE project IS NOT NULL "
            "UNION "
            "SELECT DISTINCT project FROM messages WHERE project IS NOT NULL "
            "ORDER BY project"
        ).fetchall()
        projects = [r[0] for r in rows if r[0]]
        return {"projects": projects}
    except Exception:
        return {"projects": []}
    finally:
        conn.close()


# Mount static files AFTER API routes so /api/* routes take priority
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
