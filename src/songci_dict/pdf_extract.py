from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pdfplumber

from .types import Line


@dataclass(frozen=True, slots=True)
class ExtractOptions:
    y_tolerance: float = 3.0
    keep_blank_lines: bool = True
    strip: bool = True
    page_start: int | None = None
    page_end: int | None = None


def _cluster_words_into_lines(words: list[dict[str, Any]], *, y_tolerance: float) -> list[tuple[float, str]]:
    words_sorted = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    lines: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_top: float | None = None
    for w in words_sorted:
        top = float(w["top"])
        if cur_top is None or abs(top - cur_top) <= y_tolerance:
            cur.append(w)
            if cur_top is None:
                cur_top = top
        else:
            lines.append(cur)
            cur = [w]
            cur_top = top
    if cur:
        lines.append(cur)

    out: list[tuple[float, str]] = []
    for line_words in lines:
        line_words_sorted = sorted(line_words, key=lambda w: float(w["x0"]))
        text = "".join(str(w.get("text", "")) for w in line_words_sorted)
        top = float(line_words_sorted[0]["top"])
        out.append((top, text))
    return out


def extract_pdf_lines(pdf_path: str, *, options: ExtractOptions | None = None) -> Iterator[Line]:
    opts = options or ExtractOptions()
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        start = opts.page_start if opts.page_start is not None else 1
        end = opts.page_end if opts.page_end is not None else total_pages
        start = max(1, start)
        end = min(total_pages, end)

        source_pdf = pdf_path
        for page_no in range(start, end + 1):
            page = pdf.pages[page_no - 1]
            words = page.extract_words(
                x_tolerance=1.5,
                y_tolerance=opts.y_tolerance,
                keep_blank_chars=True,
                use_text_flow=True,
            )
            clustered = _cluster_words_into_lines(words, y_tolerance=opts.y_tolerance)

            line_no = 0
            for top, text in clustered:
                line_no += 1
                t = text
                if opts.strip:
                    t = t.strip()
                if not t and not opts.keep_blank_lines:
                    continue
                yield Line(source_pdf=source_pdf, page_no=page_no, line_no=line_no, text=t, top=top)
