#!/usr/bin/env python3
"""Embedding engine — generate and store vector embeddings for conversation messages."""

import argparse
import numpy as np
import sqlite3
import sys
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent
DB_PATH = MEMORY_DIR / "memory.db"

DEFAULT_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 256


def get_model(model_name=DEFAULT_MODEL):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)


def get_unembedded_messages(conn, limit=None):
    sql = """
        SELECT m.id, m.content
        FROM messages m
        LEFT JOIN embeddings e ON e.message_id = m.id
        WHERE e.message_id IS NULL
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


def embed_messages(model_name=DEFAULT_MODEL, limit=None, batch_size=BATCH_SIZE):
    conn = sqlite3.connect(str(DB_PATH))
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
        conn.commit()
        total += len(batch)
        print(f"  {total}/{len(rows)} embedded")

    conn.close()
    print(f"Done. {total} embeddings stored.")
    return total


def search_similar(query, conn=None, model=None, top_k=10, project=None,
                   since=None, role=None, decay_halflife_days=30):
    """Search for messages semantically similar to query text."""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if model is None:
        model = get_model()

    query_vec = model.encode([query[:2048]], normalize_embeddings=True)[0]

    sql = """
        SELECT m.id, m.session_id, m.project, m.role, m.content,
               m.timestamp, m.machine, e.embedding
        FROM embeddings e
        JOIN messages m ON m.id = e.message_id
        WHERE 1=1
    """
    params = []

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
        if own_conn:
            conn.close()
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

    # Apply temporal decay: recent messages score higher
    if decay_halflife_days and decay_halflife_days > 0:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for i, meta in enumerate(metadata):
            ts = meta.get("timestamp")
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
                    # Blend: 70% semantic similarity + 30% recency
                    similarities[i] = 0.7 * similarities[i] + 0.3 * decay
                except (ValueError, TypeError):
                    pass

    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        meta = metadata[idx]
        meta.pop("embedding", None)
        meta["score"] = float(similarities[idx])
        results.append(meta)

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

    stats = sub.add_parser("stats", help="Show embedding statistics")

    args = parser.parse_args()

    if args.command == "build":
        embed_messages(model_name=args.model, limit=args.limit, batch_size=args.batch_size)
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
        conn = sqlite3.connect(str(DB_PATH))
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
