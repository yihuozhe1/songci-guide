from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager, Literal

from songci_dict.db import SongciDb

DbMode = Literal["auto", "sqlite", "postgres"]
DbKind = Literal["sqlite", "postgres"]


@dataclass(frozen=True, slots=True)
class DbTarget:
    kind: DbKind
    schema_path: str
    sqlite_path: str | None = None
    postgres_dsn: str | None = None

    def open(self) -> ContextManager[Any]:
        if self.kind == "sqlite":
            if not self.sqlite_path:
                raise RuntimeError("sqlite_path is required")
            return SongciDb(self.sqlite_path)
        if not self.postgres_dsn:
            raise RuntimeError("postgres_dsn is required")
        from songci_dict.db_postgres import SongciPostgresDb

        return SongciPostgresDb(self.postgres_dsn)


def normalize_db_mode(value: str | None) -> DbMode:
    v = str(value or "").strip().lower()
    if v in ("sqlite", "postgres", "auto"):
        return v  # type: ignore[return-value]
    return "auto"


def resolve_db_target(
    *,
    db_mode: str | None,
    sqlite_path: Path,
    sqlite_schema_path: Path,
    postgres_schema_path: Path,
    postgres_dsn: str | None,
) -> DbTarget:
    mode = normalize_db_mode(db_mode or os.getenv("DB_MODE"))
    dsn = str(postgres_dsn or os.getenv("POSTGRES_DSN") or "").strip()

    if mode == "postgres":
        if not dsn:
            raise RuntimeError("POSTGRES_DSN is required when DB_MODE=postgres")
        return DbTarget(kind="postgres", schema_path=str(postgres_schema_path), postgres_dsn=dsn)

    if mode == "sqlite":
        return DbTarget(kind="sqlite", schema_path=str(sqlite_schema_path), sqlite_path=str(sqlite_path))

    if dsn:
        return DbTarget(kind="postgres", schema_path=str(postgres_schema_path), postgres_dsn=dsn)
    return DbTarget(kind="sqlite", schema_path=str(sqlite_schema_path), sqlite_path=str(sqlite_path))
