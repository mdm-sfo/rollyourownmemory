#!/usr/bin/env python3
"""Embedding engine — generate and store vector embeddings for conversation messages."""

import argparse
import json
import numpy as np
import sqlite3
import sys
from pathlib import Path
from typing import Optional

try:
    from src.memory_db import get_conn
except ImportError:
    from memory_db import get_conn

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"
FAISS_INDEX_PATH = MEMORY_DIR / "memory.faiss"
FAISS_IDS_PATH = MEMORY_DIR / "memory_ids.json"

DEFAULT_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 256


def _get_faiss():
    """Lazy import of faiss. Returns the faiss module or None if not installed."""
    try:
        import faiss
        return faiss
    except ImportError:
        return None


def get_model(model_name=DEFAULT_MODEL):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)


def get_unembedded_messages(conn, limit=None):
    sql = """
        SELECT m.id, m.content
        FROM messages m
        WHERE m.id NOT IN (
            SELECT message_id FROM processed_messages WHERE processor = 'embeddings'
        )
        ORDER BY m.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def store_embeddings(conn, message_ids, vectors, model_name):
    conn.executemany(
        "INSERT OR IGNORE INTO embeddings (message_id, embedding, model) VALUES (?, ?, ?)",
        [(mid, vec.astype(np.float32).tobytes(), model_name)
         for mid, vec in zip(message_ids, vectors)],
    )


def update_faiss_index(message_ids: list[int], vectors: np.ndarray,
                       index_path: Optional[Path] = None,
                       ids_path: Optional[Path] = None) -> None:
    """Add new vectors to the FAISS index. Creates a new index if none exists."""
    faiss = _get_faiss()
    if faiss is None:
        return

    idx_path = index_path or FAISS_INDEX_PATH
    id_path = ids_path or FAISS_IDS_PATH

    if idx_path.exists() and id_path.exists():
        index = faiss.read_index(str(idx_path))
        with open(id_path, "r") as f:
            id_map = json.load(f)
    else:
        dim = vectors.shape[1]
        index = faiss.IndexFlatIP(dim)
        id_map = []

    # Filter out IDs already in the index
    existing_ids = set(id_map)
    new_mask = [mid not in existing_ids for mid in message_ids]
    new_ids = [mid for mid, keep in zip(message_ids, new_mask) if keep]
    new_vecs = vectors[new_mask]

    if len(new_ids) > 0:
        index.add(new_vecs.astype(np.float32))
        id_map.extend(new_ids)

        faiss.write_index(index, str(idx_path))
        with open(id_path, "w") as f:
            json.dump(id_map, f)


def rebuild_faiss_index(db_path: Optional[str] = None,
                        index_path: Optional[Path] = None,
                        ids_path: Optional[Path] = None) -> int:
    """Rebuild the FAISS index from all embeddings in SQLite.

    Returns the number of vectors indexed.
    """
    faiss = _get_faiss()
    if faiss is None:
        print("faiss-cpu is not installed. Cannot build FAISS index.", file=sys.stderr)
        return 0

    conn = get_conn(db_path or str(DB_PATH))
    rows = conn.execute(
        "SELECT message_id, embedding FROM embeddings ORDER BY message_id"
    ).fetchall()
    conn.close()

    if not rows:
        print("No embeddings found in database.")
        return 0

    ids = [r["message_id"] for r in rows]
    vecs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])

    dim = vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vecs)

    idx_path = index_path or FAISS_INDEX_PATH
    id_path = ids_path or FAISS_IDS_PATH
    faiss.write_index(index, str(idx_path))
    with open(id_path, "w") as f:
        json.dump(ids, f)

    print(f"FAISS index rebuilt: {len(ids)} vectors, dim={dim}")
    print(f"  Index: {idx_path}")
    print(f"  IDs:   {id_path}")
    return len(ids)


def embed_messages(model_name=DEFAULT_MODEL, limit=None, batch_size=BATCH_SIZE):
    conn = get_conn(str(DB_PATH))
    rows = get_unembedded_messages(conn, limit)

    if not rows:
        print("All messages already have embeddings.")
        conn.close()
        return 0

    print(f"Embedding {len(rows)} messages with {model_name}...")
    model = get_model(model_name)
    total = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [r[0] for r in batch]
        texts = [r[1][:2048] for r in batch]  # truncate very long messages

        vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        store_embeddings(conn, ids, vectors, model_name)
        # Mark messages as processed via processed_messages table
        conn.executemany(
            "INSERT OR IGNORE INTO processed_messages (message_id, processor) VALUES (?, 'embeddings')",
            [(mid,) for mid in ids],
        )
        conn.commit()
        update_faiss_index(ids, vectors)
        total += len(batch)
        print(f"  {total}/{len(rows)} embedded")

    conn.close()
    print(f"Done. {total} embeddings stored.")
    return total


def _apply_temporal_decay(results: list[dict], decay_halflife_days: float) -> list[dict]:
    """Apply temporal decay to result scores: blend 70% similarity + 30% recency."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for r in results:
        ts = r.get("timestamp")
        if ts:
            try:
                if "T" in str(ts):
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(str(ts))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_days = (now - dt).total_seconds() / 86400
                decay = 0.5 ** (age_days / decay_halflife_days)
                r["score"] = 0.7 * r["score"] + 0.3 * decay
            except (ValueError, TypeError):
                pass
    return results


def _search_faiss(query_vec: np.ndarray, conn: sqlite3.Connection,
                  top_k: int, project: Optional[str] = None,
                  since: Optional[str] = None,
                  role: Optional[str] = None) -> Optional[list[dict]]:
    """Search using FAISS index. Returns None if FAISS unavailable or index missing."""
    faiss = _get_faiss()
    if faiss is None:
        return None

    if not FAISS_INDEX_PATH.exists() or not FAISS_IDS_PATH.exists():
        return None

    index = faiss.read_index(str(FAISS_INDEX_PATH))
    with open(FAISS_IDS_PATH, "r") as f:
        id_map = json.load(f)

    if index.ntotal == 0:
        return None

    # Retrieve top_k * 3 for post-retrieval reranking
    fetch_k = min(top_k * 3, index.ntotal)
    scores, indices = index.search(query_vec.reshape(1, -1).astype(np.float32), fetch_k)

    # Map FAISS indices to message IDs
    candidate_ids = []
    candidate_scores = []
    for i, idx in enumerate(indices[0]):
        if idx < 0 or idx >= len(id_map):
            continue
        candidate_ids.append(id_map[idx])
        candidate_scores.append(float(scores[0][i]))

    if not candidate_ids:
        return None

    # Hydrate from SQLite
    placeholders = ",".join("?" * len(candidate_ids))
    sql = f"""
        SELECT m.id, m.session_id, m.project, m.role, m.content,
               m.timestamp, m.machine
        FROM messages m
        WHERE m.id IN ({placeholders})
    """
    params: list = list(candidate_ids)

    rows = conn.execute(sql, params).fetchall()
    row_map = {r["id"]: dict(r) for r in rows}

    results = []
    for mid, score in zip(candidate_ids, candidate_scores):
        if mid not in row_map:
            continue
        meta = row_map[mid]
        # Apply filters
        if project and project.lower() not in (meta.get("project") or "").lower():
            continue
        if since and (meta.get("timestamp") or "") < since:
            continue
        if role and meta.get("role") != role:
            continue
        meta["score"] = score
        results.append(meta)

    return results


def _search_bruteforce(query_vec: np.ndarray, conn: sqlite3.Connection,
                       top_k: int, project: Optional[str] = None,
                       since: Optional[str] = None,
                       role: Optional[str] = None) -> list[dict]:
    """Brute-force search using numpy dot product on all embeddings from SQLite."""
    sql = """
        SELECT m.id, m.session_id, m.project, m.role, m.content,
               m.timestamp, m.machine, e.embedding
        FROM embeddings e
        JOIN messages m ON m.id = e.message_id
        WHERE 1=1
    """
    params: list = []

    if project:
        sql += " AND m.project LIKE ?"
        params.append(f"%{project}%")
    if since:
        sql += " AND m.timestamp >= ?"
        params.append(since)
    if role:
        sql += " AND m.role = ?"
        params.append(role)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        return []

    ids = []
    embeddings = []
    metadata = []
    for row in rows:
        vec = np.frombuffer(row["embedding"], dtype=np.float32)
        ids.append(row["id"])
        embeddings.append(vec)
        metadata.append(dict(row))

    embeddings_matrix = np.stack(embeddings)
    similarities = embeddings_matrix @ query_vec

    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        meta = metadata[idx]
        meta.pop("embedding", None)
        meta["score"] = float(similarities[idx])
        results.append(meta)

    return results


def search_similar(query, conn=None, model=None, top_k=10, project=None,
                   since=None, role=None, decay_halflife_days=30):
    """Search for messages semantically similar to query text.

    Uses FAISS index when available for scalable search, falling back to
    brute-force numpy dot product when the index doesn't exist.
    Temporal decay is applied as post-retrieval reranking.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn(str(DB_PATH))

    if model is None:
        model = get_model()

    query_vec = model.encode([query[:2048]], normalize_embeddings=True)[0]

    # Try FAISS first, fall back to brute-force
    results = _search_faiss(query_vec, conn, top_k, project=project,
                            since=since, role=role)
    if results is None:
        results = _search_bruteforce(query_vec, conn, top_k, project=project,
                                     since=since, role=role)

    # Apply temporal decay as post-retrieval reranking
    if decay_halflife_days and decay_halflife_days > 0 and results:
        results = _apply_temporal_decay(results, decay_halflife_days)
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:top_k]

    if own_conn:
        conn.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="Claude Memory Embedding Engine")
    sub = parser.add_subparsers(dest="command")

    build = sub.add_parser("build", help="Generate embeddings for all unembedded messages")
    build.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name (default: {DEFAULT_MODEL})")
    build.add_argument("--limit", type=int, help="Max messages to embed")
    build.add_argument("--batch-size", type=int, default=BATCH_SIZE)

    query = sub.add_parser("search", help="Semantic search")
    query.add_argument("terms", nargs="+", help="Search query")
    query.add_argument("--limit", "-n", type=int, default=5)
    query.add_argument("--project", "-p")
    query.add_argument("--since", "-s")
    query.add_argument("--role", "-r", choices=["user", "assistant"])
    query.add_argument("--no-decay", action="store_true", help="Disable temporal decay")

    rebuild = sub.add_parser("rebuild_index", help="Rebuild FAISS index from all SQLite embeddings")

    stats = sub.add_parser("stats", help="Show embedding statistics")

    args = parser.parse_args()

    if args.command == "build":
        embed_messages(model_name=args.model, limit=args.limit, batch_size=args.batch_size)
    elif args.command == "rebuild_index":
        rebuild_faiss_index()
    elif args.command == "search":
        text = " ".join(args.terms)
        decay = 0 if args.no_decay else 30
        results = search_similar(text, top_k=args.limit, project=args.project,
                                 since=args.since, role=args.role, decay_halflife_days=decay)
        if not results:
            print("No results found.")
            sys.exit(1)
        for i, r in enumerate(results, 1):
            ts = (r.get("timestamp") or "unknown")
            if "T" in str(ts):
                ts = str(ts).split("T")[0]
            content = r["content"][:300] + "..." if len(r["content"]) > 300 else r["content"]
            print(f"\n### {i}. [{ts}] {r.get('project') or 'no-project'} "
                  f"[{r.get('machine', '')}] ({r['role']}) score={r['score']:.3f}")
            print(f"> {content}")
    elif args.command == "stats":
        conn = get_conn(str(DB_PATH))
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        embedded = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        print(f"Messages: {total}")
        print(f"Embedded: {embedded}")
        print(f"Pending:  {total - embedded}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
