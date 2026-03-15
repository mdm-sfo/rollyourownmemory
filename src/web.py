"""FastAPI web application for Claude Memory search and curation UI.

Entry point: uvicorn src.web:app --host 0.0.0.0 --port 8585
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import memory_db

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

app = FastAPI(title="Claude Memory", docs_url=None, redoc_url=None)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
