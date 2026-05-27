CREATE TABLE IF NOT EXISTS authors (
  id         BIGSERIAL PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint)
);

CREATE TABLE IF NOT EXISTS author_bios (
  id           BIGSERIAL PRIMARY KEY,
  author_id    BIGINT NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
  bio          TEXT NOT NULL,
  source_pdf   TEXT NOT NULL,
  source_page  INTEGER,
  source_order INTEGER,
  bio_hash     TEXT NOT NULL,
  created_at   BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(author_id, bio_hash)
);

CREATE TABLE IF NOT EXISTS poems (
  id                BIGSERIAL PRIMARY KEY,
  seq_no            INTEGER,
  title             TEXT NOT NULL,
  author_id         BIGINT NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
  headnote          TEXT,
  content           TEXT NOT NULL,
  annotations       TEXT,
  appendix          TEXT,
  is_required       BOOLEAN NOT NULL DEFAULT FALSE,
  proficiency       INTEGER NOT NULL DEFAULT 0,
  next_review_time  BIGINT,
  source_pdf        TEXT NOT NULL,
  source_page_start INTEGER,
  source_page_end   INTEGER,
  created_at        BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at        BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(source_pdf, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_poems_author_id ON poems(author_id);
CREATE INDEX IF NOT EXISTS idx_poems_required ON poems(is_required);
CREATE INDEX IF NOT EXISTS idx_poems_next_review_time ON poems(next_review_time);

CREATE TABLE IF NOT EXISTS extracted_lines (
  id         BIGSERIAL PRIMARY KEY,
  source_pdf TEXT NOT NULL,
  page_no    INTEGER NOT NULL,
  line_no    INTEGER NOT NULL,
  text       TEXT NOT NULL,
  role       TEXT NOT NULL,
  poem_id    BIGINT REFERENCES poems(id) ON DELETE SET NULL,
  author_id  BIGINT REFERENCES authors(id) ON DELETE SET NULL,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(source_pdf, page_no, line_no)
);

CREATE INDEX IF NOT EXISTS idx_lines_poem_role ON extracted_lines(poem_id, role);
CREATE INDEX IF NOT EXISTS idx_lines_author_role ON extracted_lines(author_id, role);

CREATE TABLE IF NOT EXISTS ai_insights (
  id         BIGSERIAL PRIMARY KEY,
  poem_id    BIGINT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source     TEXT,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(poem_id)
);

CREATE TABLE IF NOT EXISTS poem_notes (
  id         BIGSERIAL PRIMARY KEY,
  poem_id    BIGINT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  user_key   TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  updated_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint),
  UNIQUE(poem_id, user_key)
);

CREATE INDEX IF NOT EXISTS idx_poem_notes_user_key ON poem_notes(user_key);

CREATE TABLE IF NOT EXISTS parse_runs (
  id         BIGSERIAL PRIMARY KEY,
  source_pdf TEXT NOT NULL,
  parser_ver TEXT NOT NULL,
  created_at BIGINT NOT NULL DEFAULT (extract(epoch from now())::bigint)
);
