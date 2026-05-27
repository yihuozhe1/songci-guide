from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from textwrap import shorten

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from songci_dict.text_display import merge_soft_wraps


def _like(s: str) -> str:
    return f"%{s}%"


def _print_poem(
    con: sqlite3.Connection, poem_id: int, *, show_lines: bool, max_lines: int, raw: bool
) -> None:
    row = con.execute(
        """
        SELECT
          p.id, p.seq_no, p.title, a.name AS author_name,
          p.source_page_start, p.source_page_end,
          p.headnote, p.content, p.annotations, p.appendix
        FROM poems p
        JOIN authors a ON a.id = p.author_id
        WHERE p.id = ?
        """,
        (poem_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"poem_id not found: {poem_id}")

    print(f"poem_id={row[0]} seq_no={row[1]} title={row[2]} author={row[3]}")
    print(f"pages={row[4]}..{row[5]}")

    headnote = row[6] or ""
    content = row[7] or ""
    annotations = row[8]
    appendix = row[9] or ""

    if headnote.strip():
        print("\n[headnote]")
        print(headnote if raw else merge_soft_wraps(headnote))

    print("\n[content]")
    print(content if raw else merge_soft_wraps(content))

    if annotations:
        print("\n[annotations]")
        try:
            obj = json.loads(annotations)
        except Exception as e:
            print(f"<invalid json: {e}>")
            print(annotations)
        else:
            print(json.dumps(obj, ensure_ascii=False, indent=2))

    if appendix.strip():
        print("\n[appendix]")
        print(appendix if raw else merge_soft_wraps(appendix))

    if show_lines:
        print("\n[extracted_lines]")
        rows = con.execute(
            """
            SELECT page_no, line_no, role, text
            FROM extracted_lines
            WHERE poem_id = ?
            ORDER BY page_no, line_no
            """,
            (poem_id,),
        ).fetchall()
        for i, (page_no, line_no, role, text) in enumerate(rows, start=1):
            if i > max_lines:
                print(f"... ({len(rows) - max_lines} more lines)")
                break
            t = shorten(text.replace("\n", " "), width=140, placeholder="…")
            print(f"{page_no:03d}:{line_no:03d} {role:<10} {t}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/songci.sqlite")
    ap.add_argument("--poem-id", type=int, default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--author", default=None)
    ap.add_argument("--show-lines", action="store_true")
    ap.add_argument("--max-lines", type=int, default=200)
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)

    if args.poem_id is not None:
        _print_poem(con, args.poem_id, show_lines=args.show_lines, max_lines=args.max_lines, raw=args.raw)
        return 0

    if not args.title and not args.author:
        raise SystemExit("Provide --poem-id or search by --title/--author")

    sql = """
    SELECT p.id, p.seq_no, p.title, a.name, p.source_page_start, p.source_page_end
    FROM poems p
    JOIN authors a ON a.id = p.author_id
    WHERE 1=1
    """
    params: list[object] = []
    if args.title:
        sql += " AND p.title LIKE ?"
        params.append(_like(args.title))
    if args.author:
        sql += " AND a.name LIKE ?"
        params.append(_like(args.author))
    sql += " ORDER BY a.name, p.seq_no, p.id LIMIT 50"

    rows = con.execute(sql, tuple(params)).fetchall()
    if not rows:
        print("No matches.")
        return 1

    if len(rows) > 1:
        print("Multiple matches. Pick a poem_id and rerun with --poem-id.")
        for (pid, seq_no, title, author_name, p1, p2) in rows:
            print(f"poem_id={pid} seq_no={seq_no} title={title} author={author_name} pages={p1}..{p2}")
        return 2

    _print_poem(con, int(rows[0][0]), show_lines=args.show_lines, max_lines=args.max_lines, raw=args.raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
