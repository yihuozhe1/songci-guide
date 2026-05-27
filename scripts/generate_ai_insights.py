from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from songci_dict.ai_insight import build_messages_for_poem
from songci_dict.db import SongciDb
from songci_dict.zhipu import chat_completions


def _read_streamlit_secrets() -> dict[str, Any]:
    secrets_path = REPO_ROOT / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return {}
    try:
        import tomllib
    except Exception:
        return {}
    try:
        return dict(tomllib.loads(secrets_path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _get_api_key(cli_value: str | None) -> str:
    k = (cli_value or "").strip()
    if k:
        return k
    k = (os.getenv("ZHIPU_API_KEY") or "").strip()
    if k:
        return k
    secrets = _read_streamlit_secrets()
    k = str(secrets.get("ZHIPU_API_KEY") or "").strip()
    if k:
        return k
    raise SystemExit("Missing API key: pass --api-key or set env ZHIPU_API_KEY")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/songci.sqlite")
    ap.add_argument("--schema", default="schema.sql")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default="glm-5.1")
    ap.add_argument("--style", default="short300", choices=["short300", "teaching", "exam"])
    ap.add_argument("--required-only", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args()

    api_key = _get_api_key(args.api_key)
    db_path = Path(args.db)
    schema_path = Path(args.schema)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    if not schema_path.exists():
        raise SystemExit(f"Schema not found: {schema_path}")

    done = 0
    skipped = 0
    failed = 0
    offset = 0
    with SongciDb(str(db_path)) as db:
        db.init_schema(str(schema_path))
        while True:
            rows = db.list_poems_for_ai(
                required_only=bool(args.required_only),
                limit=int(args.batch),
                offset=int(offset),
            )
            if not rows:
                break
            for row in rows:
                pid = int(row["id"])
                if not args.overwrite:
                    existing = db.get_ai_insight(pid)
                    if existing and existing["content"]:
                        skipped += 1
                        continue
                poem = dict(row)
                try:
                    messages = build_messages_for_poem(poem, style=str(args.style))
                    max_tokens = 600 if str(args.style) == "short300" else 1200
                    res = chat_completions(
                        api_key=api_key,
                        model=str(args.model),
                        messages=messages,
                        max_tokens=max_tokens,
                    )
                    db.upsert_ai_insight(poem_id=pid, content=res.content, source=f"zhipu:{args.model}")
                    db.commit()
                    done += 1
                    print(f"OK poem_id={pid} title={poem.get('title')}")
                except Exception as e:
                    db.rollback()
                    failed += 1
                    print(f"FAIL poem_id={pid} title={poem.get('title')} err={type(e).__name__}: {e}")
                    if args.fail_fast:
                        raise
                if args.limit and done >= int(args.limit):
                    print(f"STOP: reached limit={args.limit}")
                    print(f"done={done} skipped={skipped} failed={failed}")
                    return 0
                if args.sleep and args.sleep > 0:
                    time.sleep(float(args.sleep))
            offset += len(rows)

    print(f"done={done} skipped={skipped} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
