from __future__ import annotations

import argparse
import os
import sqlite3
import tomllib
from pathlib import Path
from typing import Any, Iterable

import psycopg2
import psycopg2.extras


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_local_streamlit_secret(key: str) -> str:
    secrets_path = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return ""
    try:
        data = tomllib.loads(_read_text(secrets_path))
    except Exception:
        return ""
    value = data.get(key)
    return str(value or "").strip()


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _pg_connect(dsn: str) -> psycopg2.extensions.connection:
    return psycopg2.connect(dsn)


def _ensure_pg_schema(pg: psycopg2.extensions.connection, schema_path: Path) -> None:
    sql = _read_text(schema_path)
    with pg.cursor() as cur:
        cur.execute(sql)
    pg.commit()


def _iter_sqlite_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Iterable[sqlite3.Row]:
    cur = conn.execute(sql, params)
    for row in cur:
        yield row


def _set_sequences(pg: psycopg2.extensions.connection) -> None:
    pairs = [
        ("authors", "id"),
        ("author_bios", "id"),
        ("poems", "id"),
        ("extracted_lines", "id"),
        ("ai_insights", "id"),
        ("poem_notes", "id"),
        ("parse_runs", "id"),
    ]
    with pg.cursor() as cur:
        for table, col in pairs:
            cur.execute(
                f"""
                SELECT setval(
                  pg_get_serial_sequence(%s, %s),
                  GREATEST((SELECT COALESCE(MAX({col}), 1) FROM {table}), 1),
                  TRUE
                )
                """,
                (table, col),
            )
    pg.commit()


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _pg_count(pg: psycopg2.extensions.connection, table: str) -> int:
    with pg.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _print_count_report(
    sqlite: sqlite3.Connection,
    pg: psycopg2.extensions.connection,
    *,
    include_audit: bool,
) -> None:
    tables = [
        "authors",
        "author_bios",
        "poems",
        "ai_insights",
        "poem_notes",
        "parse_runs",
    ]
    if include_audit:
        tables.append("extracted_lines")

    print("Migration counts:")
    for table in tables:
        sqlite_n = _sqlite_count(sqlite, table)
        pg_n = _pg_count(pg, table)
        status = "OK" if sqlite_n == pg_n else "MISMATCH"
        print(f"- {table}: sqlite={sqlite_n}, postgres={pg_n} [{status}]")


def _migrate_authors(sqlite: sqlite3.Connection, pg: psycopg2.extensions.connection) -> None:
    sql = "SELECT id, name, created_at, updated_at FROM authors ORDER BY id"
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            cur.execute(
                """
                INSERT INTO authors(id, name, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  name = EXCLUDED.name,
                  created_at = EXCLUDED.created_at,
                  updated_at = EXCLUDED.updated_at
                """,
                (int(r["id"]), str(r["name"]), int(r["created_at"]), int(r["updated_at"])),
            )
    pg.commit()


def _migrate_poems(sqlite: sqlite3.Connection, pg: psycopg2.extensions.connection) -> None:
    sql = """
    SELECT
      id, seq_no, title, author_id, headnote, content, annotations, appendix,
      is_required, proficiency, next_review_time, source_pdf, source_page_start, source_page_end,
      created_at, updated_at
    FROM poems
    ORDER BY id
    """
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            cur.execute(
                """
                INSERT INTO poems(
                  id, seq_no, title, author_id, headnote, content, annotations, appendix,
                  is_required, proficiency, next_review_time, source_pdf, source_page_start, source_page_end,
                  created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  seq_no = EXCLUDED.seq_no,
                  title = EXCLUDED.title,
                  author_id = EXCLUDED.author_id,
                  headnote = EXCLUDED.headnote,
                  content = EXCLUDED.content,
                  annotations = EXCLUDED.annotations,
                  appendix = EXCLUDED.appendix,
                  is_required = EXCLUDED.is_required,
                  proficiency = EXCLUDED.proficiency,
                  next_review_time = EXCLUDED.next_review_time,
                  source_pdf = EXCLUDED.source_pdf,
                  source_page_start = EXCLUDED.source_page_start,
                  source_page_end = EXCLUDED.source_page_end,
                  created_at = EXCLUDED.created_at,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    int(r["id"]),
                    r["seq_no"],
                    str(r["title"]),
                    int(r["author_id"]),
                    r["headnote"],
                    str(r["content"]),
                    r["annotations"],
                    r["appendix"],
                    bool(int(r["is_required"])),
                    int(r["proficiency"]),
                    r["next_review_time"],
                    str(r["source_pdf"]),
                    r["source_page_start"],
                    r["source_page_end"],
                    int(r["created_at"]),
                    int(r["updated_at"]),
                ),
            )
    pg.commit()


def _migrate_author_bios(sqlite: sqlite3.Connection, pg: psycopg2.extensions.connection) -> None:
    sql = """
    SELECT
      id, author_id, bio, source_pdf, source_page, source_order, bio_hash, created_at
    FROM author_bios
    ORDER BY id
    """
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            cur.execute(
                """
                INSERT INTO author_bios(
                  id, author_id, bio, source_pdf, source_page, source_order, bio_hash, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  author_id = EXCLUDED.author_id,
                  bio = EXCLUDED.bio,
                  source_pdf = EXCLUDED.source_pdf,
                  source_page = EXCLUDED.source_page,
                  source_order = EXCLUDED.source_order,
                  bio_hash = EXCLUDED.bio_hash,
                  created_at = EXCLUDED.created_at
                """,
                (
                    int(r["id"]),
                    int(r["author_id"]),
                    str(r["bio"]),
                    str(r["source_pdf"]),
                    r["source_page"],
                    r["source_order"],
                    str(r["bio_hash"]),
                    int(r["created_at"]),
                ),
            )
    pg.commit()


def _migrate_ai_insights(sqlite: sqlite3.Connection, pg: psycopg2.extensions.connection) -> None:
    sql = "SELECT id, poem_id, content, source, created_at, updated_at FROM ai_insights ORDER BY id"
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            cur.execute(
                """
                INSERT INTO ai_insights(id, poem_id, content, source, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  poem_id = EXCLUDED.poem_id,
                  content = EXCLUDED.content,
                  source = EXCLUDED.source,
                  created_at = EXCLUDED.created_at,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    int(r["id"]),
                    int(r["poem_id"]),
                    str(r["content"]),
                    r["source"],
                    int(r["created_at"]),
                    int(r["updated_at"]),
                ),
            )
    pg.commit()


def _migrate_poem_notes(sqlite: sqlite3.Connection, pg: psycopg2.extensions.connection) -> None:
    sql = "SELECT id, poem_id, user_key, content, created_at, updated_at FROM poem_notes ORDER BY id"
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            cur.execute(
                """
                INSERT INTO poem_notes(id, poem_id, user_key, content, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  poem_id = EXCLUDED.poem_id,
                  user_key = EXCLUDED.user_key,
                  content = EXCLUDED.content,
                  created_at = EXCLUDED.created_at,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    int(r["id"]),
                    int(r["poem_id"]),
                    str(r["user_key"]),
                    str(r["content"]),
                    int(r["created_at"]),
                    int(r["updated_at"]),
                ),
            )
    pg.commit()


def _migrate_parse_runs(sqlite: sqlite3.Connection, pg: psycopg2.extensions.connection) -> None:
    sql = "SELECT id, source_pdf, parser_ver, created_at FROM parse_runs ORDER BY id"
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            cur.execute(
                """
                INSERT INTO parse_runs(id, source_pdf, parser_ver, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  source_pdf = EXCLUDED.source_pdf,
                  parser_ver = EXCLUDED.parser_ver,
                  created_at = EXCLUDED.created_at
                """,
                (
                    int(r["id"]),
                    str(r["source_pdf"]),
                    str(r["parser_ver"]),
                    int(r["created_at"]),
                ),
            )
    pg.commit()


def _migrate_extracted_lines(
    sqlite: sqlite3.Connection,
    pg: psycopg2.extensions.connection,
    *,
    batch_size: int,
) -> None:
    sql = """
    SELECT id, source_pdf, page_no, line_no, text, role, poem_id, author_id, created_at
    FROM extracted_lines
    ORDER BY id
    """
    tpl = "(%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    cols = "(id, source_pdf, page_no, line_no, text, role, poem_id, author_id, created_at)"
    insert_sql = f"INSERT INTO extracted_lines {cols} VALUES %s ON CONFLICT(id) DO NOTHING"

    buf: list[tuple[Any, ...]] = []
    with pg.cursor() as cur:
        for r in _iter_sqlite_rows(sqlite, sql):
            buf.append(
                (
                    int(r["id"]),
                    str(r["source_pdf"]),
                    int(r["page_no"]),
                    int(r["line_no"]),
                    str(r["text"]),
                    str(r["role"]),
                    r["poem_id"],
                    r["author_id"],
                    int(r["created_at"]),
                )
            )
            if len(buf) >= batch_size:
                psycopg2.extras.execute_values(cur, insert_sql, buf, template=tpl, page_size=batch_size)
                buf.clear()
        if buf:
            psycopg2.extras.execute_values(cur, insert_sql, buf, template=tpl, page_size=batch_size)
            buf.clear()
    pg.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", dest="sqlite_path", default=str(Path("data") / "songci.sqlite"))
    ap.add_argument("--postgres-dsn", dest="postgres_dsn", default="")
    ap.add_argument("--schema", dest="schema_path", default=str(Path("schema_postgres.sql")))
    ap.add_argument("--skip-audit", action="store_true")
    ap.add_argument("--batch-size", type=int, default=2000)
    args = ap.parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite not found: {sqlite_path}")

    dsn = str(
        args.postgres_dsn
        or os.getenv("POSTGRES_DSN")
        or _read_local_streamlit_secret("POSTGRES_DSN")
        or ""
    ).strip()
    if not dsn:
        raise SystemExit(
            "POSTGRES_DSN is required (use --postgres-dsn, env POSTGRES_DSN, "
            "or .streamlit/secrets.toml)"
        )

    schema_path = Path(args.schema_path)
    if not schema_path.exists():
        raise SystemExit(f"Schema not found: {schema_path}")

    sqlite = _sqlite_connect(sqlite_path)
    pg = _pg_connect(dsn)
    try:
        _ensure_pg_schema(pg, schema_path)
        _migrate_authors(sqlite, pg)
        _migrate_poems(sqlite, pg)
        _migrate_author_bios(sqlite, pg)
        _migrate_ai_insights(sqlite, pg)
        _migrate_poem_notes(sqlite, pg)
        _migrate_parse_runs(sqlite, pg)
        if not bool(args.skip_audit):
            _migrate_extracted_lines(sqlite, pg, batch_size=max(int(args.batch_size), 100))
        _set_sequences(pg)
        _print_count_report(sqlite, pg, include_audit=not bool(args.skip_audit))
    finally:
        try:
            sqlite.close()
        except Exception:
            pass
        try:
            pg.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
