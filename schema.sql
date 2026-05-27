PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS authors (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS author_bios (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
  bio           TEXT NOT NULL,
  source_pdf    TEXT NOT NULL,
  source_page   INTEGER,
  source_order  INTEGER,
  bio_hash      TEXT NOT NULL,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(author_id, bio_hash)
);

CREATE TABLE IF NOT EXISTS poems (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  seq_no            INTEGER,
  title             TEXT NOT NULL,
  author_id         INTEGER NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
  headnote          TEXT,
  content           TEXT NOT NULL,
  annotations       TEXT,
  appendix          TEXT,
  is_required       INTEGER NOT NULL DEFAULT 0,
  proficiency       INTEGER NOT NULL DEFAULT 0,
  next_review_time  INTEGER,
  source_pdf        TEXT NOT NULL,
  source_page_start INTEGER,
  source_page_end   INTEGER,
  created_at        INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at        INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(source_pdf, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_poems_author_id ON poems(author_id);
CREATE INDEX IF NOT EXISTS idx_poems_required ON poems(is_required);
CREATE INDEX IF NOT EXISTS idx_poems_next_review_time ON poems(next_review_time);

CREATE TABLE IF NOT EXISTS extracted_lines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_pdf   TEXT NOT NULL,
  page_no      INTEGER NOT NULL,
  line_no      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  role         TEXT NOT NULL,
  poem_id      INTEGER REFERENCES poems(id) ON DELETE SET NULL,
  author_id    INTEGER REFERENCES authors(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(source_pdf, page_no, line_no)
);

CREATE INDEX IF NOT EXISTS idx_lines_poem_role ON extracted_lines(poem_id, role);
CREATE INDEX IF NOT EXISTS idx_lines_author_role ON extracted_lines(author_id, role);

CREATE TABLE IF NOT EXISTS ai_insights (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id    INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source     TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(poem_id)
);

CREATE TABLE IF NOT EXISTS poem_notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id    INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  user_key   TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(poem_id, user_key)
);

CREATE INDEX IF NOT EXISTS idx_poem_notes_user_key ON poem_notes(user_key);

CREATE TABLE IF NOT EXISTS parse_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_pdf   TEXT NOT NULL,
  parser_ver   TEXT NOT NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
);
