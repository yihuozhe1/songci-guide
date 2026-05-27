from __future__ import annotations

import argparse
import json
import sqlite3


_POETRY_PUNCT = set("，。？！；、")


def _base_title(s: str) -> str:
    t = (s or "").strip()
    if "（" in t:
        t = t.split("（", 1)[0].strip()
    if "(" in t:
        t = t.split("(", 1)[0].strip()
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/songci.sqlite")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    poems = con.execute("SELECT COUNT(*) FROM poems").fetchone()[0]
    unassigned = con.execute("SELECT COUNT(*) FROM extracted_lines WHERE role='unassigned'").fetchone()[0]
    empty_poems = con.execute("SELECT COUNT(*) FROM poems WHERE content IS NULL OR trim(content)=''").fetchone()[0]

    bad_json = 0
    for row in con.execute("SELECT id, annotations FROM poems WHERE annotations IS NOT NULL"):
        pid = int(row["id"])
        txt = row["annotations"]
        try:
            obj = json.loads(txt)
        except Exception:
            bad_json += 1
            continue
        if not isinstance(obj, list):
            bad_json += 1

    base_titles = {
        _base_title(str(r["title"])) for r in con.execute("SELECT title FROM poems") if _base_title(str(r["title"]))
    }
    suspicious = 0
    suspicious_examples: list[str] = []
    for r in con.execute(
        """
        SELECT p.id, p.seq_no, p.title, a.name AS author_name
        FROM poems p
        JOIN authors a ON a.id = p.author_id
        ORDER BY p.seq_no, p.id
        """
    ):
        pid = int(r["id"])
        poem_base = _base_title(str(r["title"]))
        for lr in con.execute(
            """
            SELECT text
            FROM extracted_lines
            WHERE poem_id = ? AND role = 'body'
            ORDER BY page_no, line_no
            """,
            (pid,),
        ):
            t = str(lr["text"]).strip()
            if not t:
                continue
            if len(t) > 12:
                continue
            if any(ch.isdigit() for ch in t):
                continue
            if any(ch in t for ch in _POETRY_PUNCT):
                continue
            cand = _base_title(t)
            if not cand or cand == poem_base:
                continue
            if cand in base_titles:
                suspicious += 1
                if len(suspicious_examples) < 10:
                    suspicious_examples.append(
                        f"seq={r['seq_no']} author={r['author_name']} title={r['title']} :: inline_title={t}"
                    )
                break

    print(f"db={args.db}")
    print(f"poems={poems}")
    print(f"unassigned={unassigned}")
    print(f"empty_poems={empty_poems}")
    print(f"bad_annotations_json={bad_json}")
    print(f"suspicious_inline_titles={suspicious}")
    for ex in suspicious_examples:
        print(f"suspicious_example: {ex}")

    if poems <= 0:
        raise SystemExit("FAIL: poems must be > 0")
    if unassigned != 0:
        raise SystemExit("FAIL: unassigned must be 0")
    if empty_poems != 0:
        raise SystemExit("FAIL: empty_poems must be 0")
    if bad_json != 0:
        raise SystemExit("FAIL: annotations JSON must be arrays")
    if suspicious != 0:
        raise SystemExit("FAIL: suspicious inline titles detected (possible poem merging)")

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
