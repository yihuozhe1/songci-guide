from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


@dataclass(frozen=True, slots=True)
class AuthorRow:
    id: int
    name: str


class SongciPostgresDb:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn: psycopg2.extensions.connection | None = None

    def __enter__(self) -> "SongciPostgresDb":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            try:
                self.rollback()
            except Exception:
                pass
        self.close()

    @property
    def conn(self) -> psycopg2.extensions.connection:
        if self._conn is None:
            raise RuntimeError("DB not connected")
        return self._conn

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = psycopg2.connect(self.dsn)

    def close(self) -> None:
        if self._conn is None:
            return
        self._conn.close()
        self._conn = None

    def init_schema(self, schema_path: str) -> None:
        schema_sql = Path(schema_path).read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(schema_sql)
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def insert_parse_run(self, *, source_pdf: str, parser_ver: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO parse_runs(source_pdf, parser_ver) VALUES (%s, %s) RETURNING id",
                (source_pdf, parser_ver),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Failed to insert parse_run")
            return int(row[0])

    def upsert_author(self, name: str) -> AuthorRow:
        name_norm = name.strip()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO authors(name) VALUES (%s) ON CONFLICT(name) DO NOTHING",
                (name_norm,),
            )
            cur.execute("SELECT id, name FROM authors WHERE name = %s", (name_norm,))
            row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Failed to upsert author: {name_norm}")
        return AuthorRow(id=int(row[0]), name=str(row[1]))

    def insert_author_bio(
        self,
        *,
        author_id: int,
        bio: str,
        source_pdf: str,
        source_page: int | None,
        source_order: int,
    ) -> None:
        bio_norm = _normalize_text_block(bio)
        bio_hash = hashlib.sha256(bio_norm.encode("utf-8")).hexdigest()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO author_bios(author_id, bio, source_pdf, source_page, source_order, bio_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(author_id, bio_hash) DO NOTHING
                """,
                (int(author_id), bio_norm, source_pdf, source_page, int(source_order), bio_hash),
            )

    def insert_poem(
        self,
        *,
        seq_no: int | None,
        title: str,
        author_id: int,
        headnote: str | None,
        content: str,
        annotations: list[dict[str, Any]] | None,
        appendix: str | None,
        is_required: int,
        source_pdf: str,
        source_page_start: int | None,
        source_page_end: int | None,
    ) -> int:
        annotations_text: str | None = None
        if annotations is not None:
            annotations_text = json.dumps(annotations, ensure_ascii=False, separators=(",", ":"))

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO poems(
                  seq_no, title, author_id, headnote, content, annotations, appendix,
                  is_required, source_pdf, source_page_start, source_page_end
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    seq_no,
                    title.strip(),
                    int(author_id),
                    headnote,
                    content,
                    annotations_text,
                    appendix,
                    bool(int(is_required)),
                    source_pdf,
                    source_page_start,
                    source_page_end,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Failed to insert poem")
            return int(row[0])

    def list_poems(
        self,
        *,
        query: str | None,
        required_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        where: list[str] = []
        params: list[Any] = []

        if required_only:
            where.append("p.is_required = TRUE")
        if q:
            where.append("(p.title LIKE %s OR a.name LIKE %s OR p.content LIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like])

        where_sql = ""
        if where:
            where_sql = " WHERE " + " AND ".join(where)

        sql = f"""
        SELECT p.id, p.seq_no, p.title, (p.is_required::int) AS is_required, a.name AS author_name
        FROM poems p
        JOIN authors a ON a.id = p.author_id
        {where_sql}
        ORDER BY COALESCE(p.seq_no, 999999), p.id
        LIMIT %s
        """
        params.append(int(limit))

        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            return [dict(r) for r in (rows or [])]

    def get_poem(self, poem_id: int) -> dict[str, Any] | None:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                  p.id, p.seq_no, p.title, p.headnote, p.content, p.annotations, p.appendix,
                  (p.is_required::int) AS is_required,
                  a.id AS author_id, a.name AS author_name
                FROM poems p
                JOIN authors a ON a.id = p.author_id
                WHERE p.id = %s
                """,
                (int(poem_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def list_author_bios(self, author_id: int) -> list[dict[str, Any]]:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, bio, source_pdf, source_page, source_order, created_at
                FROM author_bios
                WHERE author_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (int(author_id),),
            )
            rows = cur.fetchall()
            return [dict(r) for r in (rows or [])]

    def get_ai_insight(self, poem_id: int) -> dict[str, Any] | None:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT content, source, created_at, updated_at FROM ai_insights WHERE poem_id = %s",
                (int(poem_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def upsert_ai_insight(self, *, poem_id: int, content: str, source: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_insights(poem_id, content, source)
                VALUES (%s, %s, %s)
                ON CONFLICT(poem_id) DO UPDATE SET
                  content = EXCLUDED.content,
                  source = EXCLUDED.source,
                  updated_at = (extract(epoch from now())::bigint)
                """,
                (int(poem_id), content, source),
            )

    def get_poem_note(self, poem_id: int, user_key: str) -> dict[str, Any] | None:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT content, created_at, updated_at
                FROM poem_notes
                WHERE poem_id = %s AND user_key = %s
                """,
                (int(poem_id), user_key),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def upsert_poem_note(self, *, poem_id: int, user_key: str, content: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO poem_notes(poem_id, user_key, content)
                VALUES (%s, %s, %s)
                ON CONFLICT(poem_id, user_key) DO UPDATE SET
                  content = EXCLUDED.content,
                  updated_at = (extract(epoch from now())::bigint)
                """,
                (int(poem_id), user_key, content),
            )

    def random_required_poem_id(self) -> int | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM poems WHERE is_required = TRUE ORDER BY random() LIMIT 1"
            )
            row = cur.fetchone()
            return int(row[0]) if row else None


def _normalize_text_block(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()
