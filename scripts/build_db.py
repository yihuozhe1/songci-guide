from __future__ import annotations

import argparse
import os
import sys
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from songci_dict.db import SongciDb
from songci_dict.parser_fsm import FsmParser, ParseOptions
from songci_dict.pdf_extract import ExtractOptions, extract_pdf_lines


def _validate(db: SongciDb) -> None:
    unassigned = db.count("SELECT COUNT(*) FROM extracted_lines WHERE role='unassigned'")
    if unassigned != 0:
        raise RuntimeError(f"Audit failed: unassigned lines = {unassigned}")

    empty_poems = db.count("SELECT COUNT(*) FROM poems WHERE content IS NULL OR trim(content) = ''")
    if empty_poems != 0:
        raise RuntimeError(f"Integrity failed: empty poems = {empty_poems}")

    for row in db.iter_rows("SELECT id, annotations FROM poems WHERE annotations IS NOT NULL"):
        pid = int(row["id"])
        txt = row["annotations"]
        try:
            obj = json.loads(txt)
        except Exception as e:
            raise RuntimeError(f"Invalid annotations JSON in poem_id={pid}: {e}") from e
        if not isinstance(obj, list):
            raise RuntimeError(f"Invalid annotations JSON (not array) in poem_id={pid}")


def _parse_pages(spec: str | None) -> tuple[int | None, int | None]:
    if not spec:
        return None, None
    if ":" not in spec:
        p = int(spec)
        return p, p
    a, b = spec.split(":", 1)
    start = int(a) if a.strip() else None
    end = int(b) if b.strip() else None
    return start, end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default=str(Path("data") / "songci.sqlite"))
    ap.add_argument("--schema", default=str(Path("schema.sql")))
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--pages", default=None, help="e.g. 1:20 or 3")
    ap.add_argument("--y-tolerance", type=float, default=3.0)
    ap.add_argument("--poetry-max-len", type=int, default=32)
    args = ap.parse_args()

    pdf_path = str(Path(args.pdf))
    out_path = Path(args.out)
    schema_path = str(Path(args.schema))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and args.overwrite:
        out_path.unlink()

    page_start, page_end = _parse_pages(args.pages)
    extract_opts = ExtractOptions(y_tolerance=args.y_tolerance, keep_blank_lines=True, strip=True, page_start=page_start, page_end=page_end)

    lines = list(extract_pdf_lines(pdf_path, options=extract_opts))
    if not lines:
        raise SystemExit("No lines extracted from PDF")

    os.environ.setdefault("PYTHONUTF8", "1")

    with SongciDb(str(out_path)) as db:
        db.init_schema(schema_path)
        parser = FsmParser(db, options=ParseOptions(poetry_max_len=args.poetry_max_len))
        stats = parser.parse(lines)
        _validate(db)
        db.commit()

        print("Build OK")
        print(f"DB: {out_path}")
        print(f"poems={stats['poems']} authors={stats['authors']} bios={stats['author_bios']} lines={stats['extracted_lines']} annotations={stats['annotations']}")
        print("audit=PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
