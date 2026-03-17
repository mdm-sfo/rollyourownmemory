#!/usr/bin/env python3
"""Claude Memory ETL — ingest JSONL conversation logs into SQLite + FTS5."""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from src.memory_db import get_conn
except ImportError:
    from memory_db import get_conn

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"
STATE_PATH = MEMORY_DIR / "state.json"
SCHEMA_PATH = MEMORY_DIR / "schema.sql"

# Source directories
HOME = Path.home()
HISTORY_FILE = HOME / ".claude" / "history.jsonl"
PROJECTS_DIR = HOME / ".claude" / "projects"
WORMHOLE_LOGS = HOME / "wormhole" / "claude-logs"
FACTORY_SESSIONS = HOME / ".factory" / "sessions"
CODEX_SESSIONS = HOME / ".codex" / "sessions"
CODEX_HISTORY = HOME / ".codex" / "history.jsonl"

# Record types to skip in project JONLs
SKIP_TYPES = {"progress", "file-history-snapshot", "queue-operation", "system"}


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state):
    tmp_fd, tmp_path = tempfile.mkstemp(dir=MEMORY_DIR, suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")


def derive_machine(source_file: str) -> str:
    if "wormhole/claude-logs/ec2" in source_file:
        return "ec2"
    if "wormhole/claude-logs/llm" in source_file:
        return "llm"
    if "wormhole/claude-logs/" in source_file:
        name = Path(source_file).stem
        parts = name.split("-")
        return parts[0] if parts else "unknown"
    if ".claude/projects/" in source_file:
        return "spark"
    return "spark"


def derive_project(source_file: str) -> str:
    """Extract project name from Claude Code's encoded project directory names.

    Claude Code encodes paths like /home/user/my-project as -home-user-my--project
    (single hyphens are path separators, double hyphens are literal hyphens).
    Handles .claude/projects/ and wormhole ec2-projects/ paths.

    NOTE: This function is for Claude Code paths only. Factory.ai and Codex CLI
    use different project derivation (cwd from session_start / session_meta).
    """
    parts = Path(source_file).parts
    for i, p in enumerate(parts):
        if p.endswith("projects") and i + 1 < len(parts):
            raw = parts[i + 1]
            placeholder = "\x00"
            escaped = raw.replace("--", placeholder)
            segments = escaped.lstrip("-").split("-")
            path = "/" + "/".join(s.replace(placeholder, "-") for s in segments)
            return path
    return None


def parse_history_file(filepath: str, offset: int = 0):
    """Parse ~/.claude/history.jsonl — user prompts with metadata."""
    records = []
    machine = derive_machine(str(filepath))
    with open(filepath, "rb") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            display = d.get("display", "").strip()
            if not display:
                continue

            ts = d.get("timestamp")
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts / 1000).isoformat()

            project = d.get("project")
            session_id = d.get("sessionId")

            records.append({
                "source_file": filepath,
                "session_id": session_id,
                "project": project,
                "role": "user",
                "content": display,
                "timestamp": ts,
                "machine": machine,
                "source_tool": "claude_code",
            })
        new_offset = f.tell()
    return records, new_offset


def extract_text_content(message):
    """Extract text from message content (handles both str and list of blocks)."""
    if not message or not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content.strip() if content.strip() else None
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    texts.append(t)
        return "\n\n".join(texts) if texts else None
    return None


def parse_project_jsonl(filepath: str, offset: int = 0):
    """Parse project session JSONL — full conversations."""
    records = []
    source_file = str(filepath)
    project = derive_project(source_file)
    machine = derive_machine(source_file)

    with open(filepath, "rb") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = d.get("type", "")
            if rtype in SKIP_TYPES:
                continue
            if rtype not in ("user", "assistant"):
                continue

            text = extract_text_content(d.get("message"))
            if not text:
                continue

            session_id = d.get("sessionId")
            ts = d.get("timestamp")

            records.append({
                "source_file": source_file,
                "session_id": session_id,
                "project": project,
                "role": rtype,
                "content": text,
                "timestamp": ts,
                "machine": machine,
                "source_tool": "claude_code",
            })
        new_offset = f.tell()
    return records, new_offset


def parse_interaction_jsonl(filepath: str, offset: int = 0):
    """Parse wormhole interaction logs (ec2/llm format)."""
    records = []
    source_file = str(filepath)
    machine = derive_machine(source_file)

    with open(filepath, "rb") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = d.get("ts")
            data = d.get("data", {})
            if not isinstance(data, dict):
                continue

            prompt = data.get("prompt", "").strip()
            session_id = data.get("session_id")

            if prompt:
                records.append({
                    "source_file": source_file,
                    "session_id": session_id,
                    "project": None,
                    "role": "user",
                    "content": prompt,
                    "timestamp": ts,
                    "machine": machine,
                    "source_tool": "claude_code",
                })

            # Some records have response too
            response = data.get("response", "").strip() if isinstance(data.get("response"), str) else ""
            if response:
                records.append({
                    "source_file": source_file,
                    "session_id": session_id,
                    "project": None,
                    "role": "assistant",
                    "content": response,
                    "timestamp": ts,
                    "machine": machine,
                    "source_tool": "claude_code",
                })

            # Handle sentiment/context notes as metadata
            note = d.get("note", "").strip()
            rtype = d.get("type", "")
            if note and rtype in ("sentiment", "context"):
                records.append({
                    "source_file": source_file,
                    "session_id": session_id,
                    "project": None,
                    "role": "user",
                    "content": f"[{rtype}] {note}",
                    "timestamp": ts,
                    "machine": machine,
                    "source_tool": "claude_code",
                })
        new_offset = f.tell()
    return records, new_offset


def extract_codex_content(payload):
    """Extract text from Codex content blocks (input_text / output_text types)."""
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in ("input_text", "output_text"):
            t = block.get("text", "").strip()
            if t:
                texts.append(t)
    return "\n\n".join(texts) if texts else None


def parse_factory_jsonl(filepath: str, offset: int = 0):
    """Parse Factory.ai session JSONL — user/assistant messages.

    First line is type='session_start' with session id.
    Message records have type='message' with message.role and message.content[].
    Skips: session_start, session_end, todo_state. Skips thinking content blocks.
    """
    records = []
    source_file = str(filepath)
    project = None
    session_id = None

    with open(filepath, "rb") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = d.get("type", "")

            if rtype == "session_start":
                session_id = d.get("id")
                project = d.get("cwd")
                continue

            if rtype != "message":
                continue

            message = d.get("message")
            if not isinstance(message, dict):
                continue

            role = message.get("role")
            if role not in ("user", "assistant"):
                continue

            text = extract_text_content(message)
            if not text:
                continue

            ts = d.get("timestamp")

            records.append({
                "source_file": source_file,
                "session_id": session_id,
                "project": project,
                "role": role,
                "content": text,
                "timestamp": ts,
                "machine": "spark",
                "source_tool": "factory",
            })
        new_offset = f.tell()
    return records, new_offset


def parse_codex_session_jsonl(filepath: str, offset: int = 0):
    """Parse Codex CLI session JSONL — user/assistant messages.

    First line is type='session_meta' with payload.id (session UUID) and payload.cwd.
    Message records are type='response_item' with payload.type='message'.
    Content blocks use input_text/output_text instead of text.
    Ingests both commentary and final_answer phases.
    Skips: developer role, reasoning, function_call, function_call_output, web_search_call.
    """
    records = []
    source_file = str(filepath)
    session_id = None
    project = None

    with open(filepath, "rb") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = d.get("type", "")

            if rtype == "session_meta":
                payload = d.get("payload", {})
                session_id = payload.get("id")
                project = payload.get("cwd")
                continue

            if rtype != "response_item":
                continue

            payload = d.get("payload", {})
            if not isinstance(payload, dict):
                continue

            payload_type = payload.get("type")
            if payload_type != "message":
                continue

            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue

            text = extract_codex_content(payload)
            if not text:
                continue

            ts = d.get("timestamp")

            records.append({
                "source_file": source_file,
                "session_id": session_id,
                "project": project,
                "role": role,
                "content": text,
                "timestamp": ts,
                "machine": "spark",
                "source_tool": "codex",
            })
        new_offset = f.tell()
    return records, new_offset


def parse_codex_history(filepath: str, offset: int = 0):
    """Parse ~/.codex/history.jsonl — user prompts with epoch-second timestamps."""
    records = []
    source_file = str(filepath)

    with open(filepath, "rb") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = d.get("text", "").strip()
            if not text:
                continue

            ts = d.get("ts")
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts).isoformat()

            session_id = d.get("session_id")

            records.append({
                "source_file": source_file,
                "session_id": session_id,
                "project": None,
                "role": "user",
                "content": text,
                "timestamp": ts,
                "machine": "spark",
                "source_tool": "codex",
            })
        new_offset = f.tell()
    return records, new_offset


def discover_sources():
    """Find all JSONL source files."""
    sources = []

    # history.jsonl
    if HISTORY_FILE.exists():
        sources.append(("history", str(HISTORY_FILE)))

    # Project session JONLs
    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if project_dir.is_dir():
                for jsonl in project_dir.glob("*.jsonl"):
                    sources.append(("project", str(jsonl)))

    # Wormhole interaction logs (direct JSONL files like ec2-claude-interactions.jsonl)
    if WORMHOLE_LOGS.exists():
        for jsonl in WORMHOLE_LOGS.glob("*.jsonl"):
            sources.append(("interaction", str(jsonl)))

    # Rsynced EC2 history
    ec2_history = WORMHOLE_LOGS / "ec2-history.jsonl"
    if ec2_history.exists():
        sources.append(("history", str(ec2_history)))

    # Rsynced EC2 project JONLs
    ec2_projects = WORMHOLE_LOGS / "ec2-projects"
    if ec2_projects.exists():
        for project_dir in ec2_projects.iterdir():
            if project_dir.is_dir():
                for jsonl in project_dir.glob("*.jsonl"):
                    sources.append(("project", str(jsonl)))

    # Factory.ai session JONLs
    if FACTORY_SESSIONS.exists():
        for subdir in FACTORY_SESSIONS.iterdir():
            if subdir.is_dir():
                for jsonl in subdir.glob("*.jsonl"):
                    sources.append(("factory_session", str(jsonl)))

    # Codex CLI session JONLs (recursive: sessions/YYYY/MM/DD/*.jsonl)
    if CODEX_SESSIONS.exists():
        for jsonl in CODEX_SESSIONS.rglob("*.jsonl"):
            sources.append(("codex_session", str(jsonl)))

    # Codex history
    if CODEX_HISTORY.exists():
        sources.append(("codex_history", str(CODEX_HISTORY)))

    return sources


def insert_records(conn, records):
    """Insert records with deduplication via INSERT OR IGNORE."""
    conn.executemany(
        """INSERT OR IGNORE INTO messages
           (source_file, session_id, project, role, content, timestamp, machine, source_tool)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(r["source_file"], r["session_id"], r["project"],
          r["role"], r["content"], r["timestamp"], r["machine"],
          r.get("source_tool", "claude_code"))
         for r in records],
    )


def main():
    parser = argparse.ArgumentParser(description="Claude Memory ETL")
    parser.add_argument("--full", action="store_true",
                        help="Full re-ingest (ignore cursor state)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress output when nothing changed")
    args = parser.parse_args()

    state = {} if args.full else load_state()
    sources = discover_sources()

    conn = get_conn(str(DB_PATH))
    init_db(conn)

    before_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    total_records = 0

    for src_type, filepath in sources:
        file_size = os.path.getsize(filepath)
        prev_offset = state.get(filepath, 0)

        if not args.full and prev_offset >= file_size:
            continue  # No new data

        offset = 0 if args.full else prev_offset

        if src_type == "history":
            records, new_offset = parse_history_file(filepath, offset)
        elif src_type == "project":
            records, new_offset = parse_project_jsonl(filepath, offset)
        elif src_type == "interaction":
            records, new_offset = parse_interaction_jsonl(filepath, offset)
        elif src_type == "factory_session":
            records, new_offset = parse_factory_jsonl(filepath, offset)
        elif src_type == "codex_session":
            records, new_offset = parse_codex_session_jsonl(filepath, offset)
        elif src_type == "codex_history":
            records, new_offset = parse_codex_history(filepath, offset)
        else:
            continue

        if records:
            insert_records(conn, records)
            total_records += len(records)

        state[filepath] = new_offset

    conn.commit()
    after_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    new_records = after_count - before_count

    save_state(state)
    conn.close()

    if args.quiet and new_records == 0:
        return

    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] Processed {len(sources)} sources, "
          f"{total_records} records parsed, {new_records} new inserted "
          f"(total: {after_count})")


if __name__ == "__main__":
    main()
