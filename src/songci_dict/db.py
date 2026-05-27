from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthorRow:
    id: int
    name: str


class SongciDb:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "SongciDb":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("DB not connected")
        return self._conn

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")

    def close(self) -> None:
        if self._conn is None:
            return
        self._conn.close()
        self._conn = None

    def init_schema(self, schema_path: str) -> None:
        schema_sql = Path(schema_path).read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)
        self.conn.commit()

    def begin(self) -> sqlite3.Cursor:
        return self.conn.cursor()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def insert_parse_run(self, *, source_pdf: str, parser_ver: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO parse_runs(source_pdf, parser_ver) VALUES (?, ?)",
            (source_pdf, parser_ver),
        )
        return int(cur.lastrowid)

    def upsert_author(self, name: str) -> AuthorRow:
        name_norm = name.strip()
        self.conn.execute(
            "INSERT INTO authors(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (name_norm,),
        )
        row = self.conn.execute("SELECT id, name FROM authors WHERE name = ?", (name_norm,)).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to upsert author: {name_norm}")
        return AuthorRow(id=int(row["id"]), name=str(row["name"]))

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
        self.conn.execute(
            """
            INSERT INTO author_bios(author_id, bio, source_pdf, source_page, source_order, bio_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(author_id, bio_hash) DO NOTHING
            """,
            (author_id, bio_norm, source_pdf, source_page, source_order, bio_hash),
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

        cur = self.conn.execute(
            """
            INSERT INTO poems(
              seq_no, title, author_id, headnote, content, annotations, appendix,
              is_required, source_pdf, source_page_start, source_page_end
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seq_no,
                title.strip(),
                author_id,
                headnote,
                content,
                annotations_text,
                appendix,
                int(is_required),
                source_pdf,
                source_page_start,
                source_page_end,
            ),
        )
        return int(cur.lastrowid)

    def insert_extracted_line(
        self,
        *,
        source_pdf: str,
        page_no: int,
        line_no: int,
        text: str,
        role: str,
        poem_id: int | None,
        author_id: int | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO extracted_lines(source_pdf, page_no, line_no, text, role, poem_id, author_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source_pdf, page_no, line_no, text, role, poem_id, author_id),
        )

    def update_extracted_lines_poem_id(
        self, *, source_pdf: str, poem_id: int, keys: Iterable[tuple[int, int]]
    ) -> None:
        self.conn.executemany(
            "UPDATE extracted_lines SET poem_id=? WHERE source_pdf=? AND page_no=? AND line_no=?",
            ((poem_id, source_pdf, page_no, line_no) for (page_no, line_no) in keys),
        )

    def count(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        return int(row[0])

    def iter_rows(self, sql: str, params: tuple[Any, ...] = ()) -> Iterator[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        for row in cur:
            yield row

    def list_poems(
        self,
        *,
        query: str | None,
        required_only: bool = False,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        q = (query or "").strip()
        where: list[str] = []
        params: list[Any] = []

        if required_only:
            where.append("p.is_required = 1")
        if q:
            where.append("(p.title LIKE ? OR a.name LIKE ? OR p.content LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])

        where_sql = ""
        if where:
            where_sql = " WHERE " + " AND ".join(where)

        sql = f"""
        SELECT p.id, p.seq_no, p.title, p.is_required, a.name AS author_name
        FROM poems p
        JOIN authors a ON a.id = p.author_id
        {where_sql}
        ORDER BY COALESCE(p.seq_no, 999999), p.id
        LIMIT ?
        """
        params.append(int(limit))
        return list(self.conn.execute(sql, tuple(params)).fetchall())

    def get_poem(self, poem_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
              p.id, p.seq_no, p.title, p.headnote, p.content, p.annotations, p.appendix, p.is_required,
              a.id AS author_id, a.name AS author_name
            FROM poems p
            JOIN authors a ON a.id = p.author_id
            WHERE p.id = ?
            """,
            (int(poem_id),),
        ).fetchone()

    def list_author_bios(self, author_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT id, bio, source_pdf, source_page, source_order, created_at
                FROM author_bios
                WHERE author_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (int(author_id),),
            ).fetchall()
        )

    def get_ai_insight(self, poem_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT content, source, created_at, updated_at FROM ai_insights WHERE poem_id = ?",
            (int(poem_id),),
        ).fetchone()

    def upsert_ai_insight(self, *, poem_id: int, content: str, source: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO ai_insights(poem_id, content, source)
            VALUES (?, ?, ?)
            ON CONFLICT(poem_id) DO UPDATE SET
              content = excluded.content,
              source = excluded.source,
              updated_at = unixepoch()
            """,
            (int(poem_id), content, source),
        )

    def get_poem_note(self, poem_id: int, user_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT content, created_at, updated_at
            FROM poem_notes
            WHERE poem_id = ? AND user_key = ?
            """,
            (int(poem_id), user_key),
        ).fetchone()

    def upsert_poem_note(self, *, poem_id: int, user_key: str, content: str) -> None:
        self.conn.execute(
            """
            INSERT INTO poem_notes(poem_id, user_key, content)
            VALUES (?, ?, ?)
            ON CONFLICT(poem_id, user_key) DO UPDATE SET
              content = excluded.content,
              updated_at = unixepoch()
            """,
            (int(poem_id), user_key, content),
        )

    def list_poems_for_ai(
        self,
        *,
        required_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        where_sql = "WHERE p.is_required = 1" if required_only else ""
        return list(
            self.conn.execute(
                f"""
                SELECT
                  p.id, p.title, p.headnote, p.content, p.annotations, p.appendix,
                  a.name AS author_name
                FROM poems p
                JOIN authors a ON a.id = p.author_id
                {where_sql}
                ORDER BY COALESCE(p.seq_no, 999999), p.id
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            ).fetchall()
        )

    def random_required_poem_id(self) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM poems WHERE is_required = 1 ORDER BY random() LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])


def _normalize_text_block(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()
