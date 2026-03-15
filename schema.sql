-- Claude Memory System — SQLite Schema
-- FTS5 keyword search + vector embeddings over Claude Code conversation logs

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    session_id TEXT,
    project TEXT,
    role TEXT NOT NULL,        -- 'user' or 'assistant'
    content TEXT NOT NULL,
    timestamp TEXT,            -- ISO 8601
    machine TEXT,              -- derived from source path
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup
    ON messages(source_file, session_id, role, timestamp);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id);

CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);

CREATE INDEX IF NOT EXISTS idx_messages_project
    ON messages(project);

-- Embeddings stored separately for efficient bulk operations
CREATE TABLE IF NOT EXISTS embeddings (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,      -- float32 numpy array stored as bytes
    model TEXT NOT NULL,           -- model name used to generate embedding
    created_at TEXT DEFAULT (datetime('now'))
);

-- Distilled facts extracted from conversations
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    project TEXT,
    fact TEXT NOT NULL,
    category TEXT,                 -- 'preference', 'decision', 'learning', 'context', 'tool', 'pattern'
    confidence REAL DEFAULT 1.0,
    source_message_id INTEGER REFERENCES messages(id),
    timestamp TEXT,
    last_validated TEXT,           -- ISO 8601 timestamp for fact decay tracking
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_project ON facts(project);

-- Entities and relationships extracted from conversations
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,     -- 'project', 'library', 'person', 'service', 'file', 'concept'
    first_seen TEXT,
    last_seen TEXT,
    mention_count INTEGER DEFAULT 1,
    metadata TEXT                  -- JSON blob for extra attributes
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type
    ON entities(name, entity_type);

CREATE TABLE IF NOT EXISTS entity_mentions (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    session_id TEXT,
    timestamp TEXT
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_entity ON entity_mentions(entity_id);

-- Tracks which processor has handled each message
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    processor TEXT NOT NULL,
    processed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (message_id, processor)
);

CREATE INDEX IF NOT EXISTS idx_processed_messages_processor ON processed_messages(processor);

-- FTS5 full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    project,
    content='messages',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact,
    category,
    content='facts',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, project)
    VALUES (new.id, new.content, new.project);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, project)
    VALUES ('delete', old.id, old.content, old.project);
    INSERT INTO messages_fts(rowid, content, project)
    VALUES (new.id, new.content, new.project);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, project)
    VALUES ('delete', old.id, old.content, old.project);
END;

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact, category)
    VALUES (new.id, new.fact, new.category);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact, category)
    VALUES ('delete', old.id, old.fact, old.category);
    INSERT INTO facts_fts(rowid, fact, category)
    VALUES (new.id, new.fact, new.category);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact, category)
    VALUES ('delete', old.id, old.fact, old.category);
END;
