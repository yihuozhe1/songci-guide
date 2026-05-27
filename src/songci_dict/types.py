from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Line:
    source_pdf: str
    page_no: int
    line_no: int
    text: str
    top: float | None = None
