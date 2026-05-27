from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .db import SongciDb
from .types import Line


class State(str, Enum):
    S_SEEK_TITLE = "S_SEEK_TITLE"
    S_EXPECT_AUTHOR = "S_EXPECT_AUTHOR"
    S_MAYBE_BIO_OR_HEADNOTE_OR_BODY = "S_MAYBE_BIO_OR_HEADNOTE_OR_BODY"
    S_HEADNOTE = "S_HEADNOTE"
    S_BODY = "S_BODY"
    S_ANNOTATION = "S_ANNOTATION"
    S_APPENDIX = "S_APPENDIX"
    S_AUTHOR_BIO = "S_AUTHOR_BIO"


@dataclass(frozen=True, slots=True)
class ParseOptions:
    poetry_max_len: int = 32
    strict_unassigned_zero: bool = True
    parser_ver: str = "fsm-v1"


_TITLE_RE = re.compile(r"^\s*([*＊★☆])?\s*(\d+)\s*[\.．、]\s*(.+?)\s*$")
_POETRY_PUNCT = set("，。？！；、")
_TERM_HEADER_RE = re.compile(r"^\s*\d{4}年.*学期\s*$")
_TOC_ENTRY_RE = re.compile(r"^\s*(?P<marker>[*＊★☆])?(?P<title>.+?)\s*\.{3,}\s*(?P<page>\d+)\s*$")


def _parse_required_prefix_title(text: str) -> tuple[int, str]:
    t = text.strip()
    if not t:
        return 0, ""
    if t[0] in "*＊★☆":
        return 1, t[1:].lstrip()
    return 0, t


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


@dataclass(slots=True)
class _Classified:
    raw: str
    is_blank: bool
    title: str | None = None
    seq_no: int | None = None
    is_required: int = 0
    is_annotation_start: bool = False
    is_appendix_start: bool = False
    looks_like_poetry: bool = False


class LineClassifier:
    def __init__(self, *, poetry_max_len: int = 32):
        self.poetry_max_len = poetry_max_len

    def classify(self, text: str) -> _Classified:
        raw = text
        t = raw.strip()
        is_blank = t == ""

        m = _TITLE_RE.match(raw)
        if m:
            is_required = 1 if m.group(1) else 0
            seq_no = int(m.group(2))
            title = m.group(3).strip()
            return _Classified(
                raw=raw,
                is_blank=is_blank,
                title=title,
                seq_no=seq_no,
                is_required=is_required,
                is_annotation_start=False,
                is_appendix_start=False,
                looks_like_poetry=False,
            )

        is_annotation_start = raw.lstrip().startswith("[")
        is_appendix_start = raw.lstrip().startswith("【附录】")
        looks_like_poetry = self._looks_like_poetry(t, is_annotation_start=is_annotation_start, is_appendix_start=is_appendix_start)
        return _Classified(
            raw=raw,
            is_blank=is_blank,
            title=None,
            seq_no=None,
            is_required=0,
            is_annotation_start=is_annotation_start,
            is_appendix_start=is_appendix_start,
            looks_like_poetry=looks_like_poetry,
        )

    def _looks_like_poetry(self, t: str, *, is_annotation_start: bool, is_appendix_start: bool) -> bool:
        if not t:
            return False
        if is_annotation_start or is_appendix_start:
            return False
        if any(ch.isdigit() for ch in t):
            return False
        if len(t) > self.poetry_max_len:
            punct_count = sum(1 for ch in t if ch in _POETRY_PUNCT)
            if len(t) <= self.poetry_max_len * 3 and punct_count >= 2 and ("。" in t or "！" in t or "？" in t):
                return True
            return False
        punct_count = sum(1 for ch in t if ch in _POETRY_PUNCT)
        if punct_count >= 1:
            return True
        if "。" in t or "，" in t:
            return True
        return False


@dataclass(slots=True)
class PoemAccumulator:
    source_pdf: str
    seq_no: int | None
    title: str
    is_required: int
    author_id: int | None = None
    headnote_lines: list[str] = field(default_factory=list)
    body_lines: list[str] = field(default_factory=list)
    appendix_lines: list[str] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    extracted_keys: list[tuple[int, int]] = field(default_factory=list)
    expect_bio: bool = False

    def touch_page(self, page_no: int) -> None:
        if self.page_start is None:
            self.page_start = page_no
        self.page_end = page_no

    def add_key(self, page_no: int, line_no: int) -> None:
        self.extracted_keys.append((page_no, line_no))

    def add_headnote(self, text: str) -> None:
        if text.strip():
            self.headnote_lines.append(text.strip())

    def add_body(self, text: str) -> None:
        if text.strip():
            self.body_lines.append(text.strip())

    def add_appendix(self, text: str) -> None:
        if text.strip():
            self.appendix_lines.append(text.strip())

    def start_annotation_item(self, line: Line) -> None:
        item = _parse_annotation_start(line.text, page_no=line.page_no)
        self.annotations.append(item)

    def append_annotation_continuation(self, text: str) -> None:
        if not self.annotations:
            self.annotations.append(
                {"raw": text, "term": None, "sep": None, "definition": None, "continuations": [], "page_no": None}
            )
        item = self.annotations[-1]
        item.setdefault("continuations", [])
        item["continuations"].append(text.strip())

    def finalize_headnote(self) -> str | None:
        if not self.headnote_lines:
            return None
        return "\n".join(self.headnote_lines).strip()

    def finalize_content(self) -> str:
        return "\n".join(self.body_lines).strip()

    def finalize_appendix(self) -> str | None:
        if not self.appendix_lines:
            return None
        return "\n".join(self.appendix_lines).strip()


def _parse_annotation_start(text: str, *, page_no: int) -> dict[str, Any]:
    raw = text.strip()
    term = None
    sep = None
    definition = None
    continuations: list[str] = []

    m = re.match(r"^\s*\[([^\]]+)\]\s*([：:])?\s*(.*)$", raw)
    if m:
        term = m.group(1).strip()
        sep = m.group(2) if m.group(2) else None
        rest = (m.group(3) or "").strip()
        definition = rest if rest else None
    return {
        "raw": raw,
        "term": term,
        "sep": sep,
        "definition": definition,
        "continuations": continuations,
        "page_no": page_no,
    }


class FsmParser:
    def __init__(self, db: SongciDb, *, options: ParseOptions | None = None):
        self.db = db
        self.options = options or ParseOptions()
        self.classifier = LineClassifier(poetry_max_len=self.options.poetry_max_len)

    def _extract_required_titles_from_toc(self, lines: list[Line]) -> set[str]:
        required: set[str] = set()
        for line in lines:
            m = _TOC_ENTRY_RE.match(line.text)
            if not m:
                continue
            if not m.group("marker"):
                continue
            title = m.group("title").strip()
            if title:
                required.add(_norm_title(title))
        return required

    def parse(self, lines: list[Line]) -> dict[str, int]:
        if not lines:
            return {"poems": 0, "authors": 0, "author_bios": 0, "extracted_lines": 0, "annotations": 0}

        source_pdf = lines[0].source_pdf
        self.db.insert_parse_run(source_pdf=source_pdf, parser_ver=self.options.parser_ver)

        required_titles = self._extract_required_titles_from_toc(lines)

        state = State.S_SEEK_TITLE
        current: PoemAccumulator | None = None
        current_author_id: int | None = None
        section_author_id: int | None = None

        bio_buf: list[str] = []
        bio_source_page: int | None = None
        bio_order = 0

        def flush_bio_if_any() -> None:
            nonlocal bio_buf, bio_source_page, bio_order, current_author_id
            if current_author_id is None:
                bio_buf = []
                bio_source_page = None
                return
            bio_text = "\n".join([ln for ln in bio_buf if ln.strip()]).strip()
            if not bio_text:
                bio_buf = []
                bio_source_page = None
                return
            bio_order += 1
            self.db.insert_author_bio(
                author_id=current_author_id,
                bio=bio_text,
                source_pdf=source_pdf,
                source_page=bio_source_page,
                source_order=bio_order,
            )
            bio_buf = []
            bio_source_page = None

        def flush_poem_if_any() -> None:
            nonlocal current
            flush_bio_if_any()
            if current is None:
                return
            if current.author_id is None:
                raise RuntimeError(f"Poem missing author: {current.title}")
            content = current.finalize_content()
            if not content:
                raise RuntimeError(f"Poem content empty: {current.title}")
            title = current.title
            if "（" not in title and "(" not in title and current.body_lines:
                first_line = current.body_lines[0].strip()
                if first_line:
                    cut = len(first_line)
                    for i, ch in enumerate(first_line):
                        if ch in _POETRY_PUNCT:
                            cut = i
                            break
                    first_phrase = first_line[:cut].strip()
                    if first_phrase and first_phrase != title and len(first_phrase) <= 20:
                        title = f"{title}（{first_phrase}）"
            is_required = current.is_required
            if not is_required and _norm_title(title) in required_titles:
                is_required = 1
            poem_id = self.db.insert_poem(
                seq_no=current.seq_no,
                title=title,
                author_id=current.author_id,
                headnote=current.finalize_headnote(),
                content=content,
                annotations=current.annotations if current.annotations else None,
                appendix=current.finalize_appendix(),
                is_required=is_required,
                source_pdf=current.source_pdf,
                source_page_start=current.page_start,
                source_page_end=current.page_end,
            )
            self.db.update_extracted_lines_poem_id(source_pdf=source_pdf, poem_id=poem_id, keys=current.extracted_keys)
            current = None

        extracted_count = 0
        unknown_author = self.db.upsert_author("佚名")
        idx = 0
        next_seq_no = 1

        while idx < len(lines):
            line = lines[idx]
            cls = self.classifier.classify(line.text)

            is_author_section = self._is_author_section_heading(lines, idx)
            is_unnumbered_title = cls.title is None and (not is_author_section) and self._is_probable_unnumbered_title(lines, idx)
            is_inline_title = (
                current is not None
                and bool(current.body_lines)
                and state in (State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY, State.S_HEADNOTE, State.S_BODY)
                and (not is_author_section)
                and cls.title is None
                and self._is_probable_inline_title(lines, idx)
            )

            if cls.title is not None:
                flush_poem_if_any()
                if cls.seq_no is None:
                    seq_no = next_seq_no
                    next_seq_no += 1
                else:
                    seq_no = cls.seq_no
                    next_seq_no = max(next_seq_no, seq_no + 1)
                current = PoemAccumulator(
                    source_pdf=source_pdf,
                    seq_no=seq_no,
                    title=cls.title,
                    is_required=cls.is_required,
                )
                current.touch_page(line.page_no)
                current.add_key(line.page_no, line.line_no)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="title",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                if section_author_id is not None:
                    current.author_id = section_author_id
                    current_author_id = section_author_id
                    current.expect_bio = False
                    state = State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
                else:
                    state = State.S_EXPECT_AUTHOR
                idx += 1
                continue

            if is_author_section:
                flush_poem_if_any()
                flush_bio_if_any()
                author = self.db.upsert_author(line.text)
                section_author_id = author.id
                current_author_id = author.id
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="author",
                    poem_id=None,
                    author_id=author.id,
                )
                extracted_count += 1
                state = State.S_AUTHOR_BIO
                idx += 1
                continue

            if is_inline_title:
                carry_author_id = current_author_id or section_author_id or unknown_author.id
                flush_poem_if_any()
                is_required, title = _parse_required_prefix_title(line.text)
                current = PoemAccumulator(
                    source_pdf=source_pdf,
                    seq_no=next_seq_no,
                    title=title,
                    is_required=is_required,
                )
                next_seq_no += 1
                current.author_id = carry_author_id
                current_author_id = carry_author_id
                current.expect_bio = False
                current.touch_page(line.page_no)
                current.add_key(line.page_no, line.line_no)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="title",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                state = State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
                idx += 1
                continue

            if is_unnumbered_title:
                flush_poem_if_any()
                is_required, title = _parse_required_prefix_title(line.text)
                current = PoemAccumulator(
                    source_pdf=source_pdf,
                    seq_no=next_seq_no,
                    title=title,
                    is_required=is_required,
                )
                next_seq_no += 1
                current.touch_page(line.page_no)
                current.add_key(line.page_no, line.line_no)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="title",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                if section_author_id is not None:
                    current.author_id = section_author_id
                    current_author_id = section_author_id
                    current.expect_bio = False
                    state = State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
                else:
                    state = State.S_EXPECT_AUTHOR
                idx += 1
                continue

            if state == State.S_SEEK_TITLE:
                role = "whitespace" if cls.is_blank else "noise"
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role=role,
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            if state == State.S_AUTHOR_BIO:
                if cls.is_blank:
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="whitespace",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    idx += 1
                    continue
                if self._is_page_number_line(line.text):
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="noise",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    idx += 1
                    continue
                if current_author_id is not None:
                    if bio_source_page is None:
                        bio_source_page = line.page_no
                    bio_buf.append(line.text)
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="bio",
                        poem_id=None,
                        author_id=current_author_id,
                    )
                    extracted_count += 1
                    idx += 1
                    continue

            if current is None:
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="unassigned",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            if self._is_page_number_line(line.text):
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="noise",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            current.touch_page(line.page_no)
            current.add_key(line.page_no, line.line_no)

            if state == State.S_EXPECT_AUTHOR:
                if cls.is_blank:
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="whitespace",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    idx += 1
                    continue

                if current is not None and self._is_title_parenthetical(line.text) and "（" not in current.title and "(" not in current.title:
                    current.title = current.title + line.text.strip()
                    current.touch_page(line.page_no)
                    current.add_key(line.page_no, line.line_no)
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="title",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    idx += 1
                    continue

                if self._is_term_header_line(line.text):
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="noise",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    idx += 1
                    continue

                if self._looks_like_author_line(lines, idx):
                    author = self.db.upsert_author(line.text)
                    current.author_id = author.id
                    current_author_id = author.id
                    current.expect_bio = self._is_first_author_bio_candidate(author.id)
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="author",
                        poem_id=None,
                        author_id=author.id,
                    )
                    extracted_count += 1
                    state = State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
                    idx += 1
                    continue

                current.author_id = unknown_author.id
                current_author_id = unknown_author.id
                current.expect_bio = False
                state = State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY
                continue

            if cls.is_blank:
                if state == State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY and current.expect_bio and bio_buf:
                    flush_bio_if_any()
                    current.expect_bio = False
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="whitespace",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            if cls.is_annotation_start:
                flush_bio_if_any()
                current.start_annotation_item(line)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="annotation",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                state = State.S_ANNOTATION
                idx += 1
                continue

            if cls.is_appendix_start:
                flush_bio_if_any()
                current.add_appendix(line.text)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="appendix",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                state = State.S_APPENDIX
                idx += 1
                continue

            if state == State.S_MAYBE_BIO_OR_HEADNOTE_OR_BODY:
                if cls.looks_like_poetry:
                    flush_bio_if_any()
                    current.add_body(line.text)
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="body",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    state = State.S_BODY
                    idx += 1
                    continue

                if current.expect_bio and current_author_id is not None:
                    if bio_source_page is None:
                        bio_source_page = line.page_no
                    bio_buf.append(line.text)
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="bio",
                        poem_id=None,
                        author_id=current_author_id,
                    )
                    extracted_count += 1
                    idx += 1
                    continue

                flush_bio_if_any()
                current.add_headnote(line.text)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="headnote",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                state = State.S_HEADNOTE
                idx += 1
                continue

            if state == State.S_HEADNOTE:
                if cls.looks_like_poetry:
                    current.add_body(line.text)
                    self.db.insert_extracted_line(
                        source_pdf=source_pdf,
                        page_no=line.page_no,
                        line_no=line.line_no,
                        text=line.text,
                        role="body",
                        poem_id=None,
                        author_id=None,
                    )
                    extracted_count += 1
                    state = State.S_BODY
                    idx += 1
                    continue
                current.add_headnote(line.text)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="headnote",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            if state == State.S_BODY:
                current.add_body(line.text)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="body",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            if state == State.S_ANNOTATION:
                current.append_annotation_continuation(line.text)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="annotation",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            if state == State.S_APPENDIX:
                current.add_appendix(line.text)
                self.db.insert_extracted_line(
                    source_pdf=source_pdf,
                    page_no=line.page_no,
                    line_no=line.line_no,
                    text=line.text,
                    role="appendix",
                    poem_id=None,
                    author_id=None,
                )
                extracted_count += 1
                idx += 1
                continue

            self.db.insert_extracted_line(
                source_pdf=source_pdf,
                page_no=line.page_no,
                line_no=line.line_no,
                text=line.text,
                role="unassigned",
                poem_id=None,
                author_id=None,
            )
            extracted_count += 1
            idx += 1

        flush_bio_if_any()
        flush_poem_if_any()

        unassigned = self.db.count("SELECT COUNT(*) FROM extracted_lines WHERE role='unassigned'")
        if self.options.strict_unassigned_zero and unassigned != 0:
            raise RuntimeError(f"Unassigned lines exist: {unassigned}")

        return {
            "poems": self.db.count("SELECT COUNT(*) FROM poems"),
            "authors": self.db.count("SELECT COUNT(*) FROM authors"),
            "author_bios": self.db.count("SELECT COUNT(*) FROM author_bios"),
            "extracted_lines": extracted_count,
            "annotations": self._count_annotation_items(),
        }

    def _count_annotation_items(self) -> int:
        total = 0
        for row in self.db.iter_rows("SELECT annotations FROM poems WHERE annotations IS NOT NULL"):
            txt = row["annotations"]
            if not txt:
                continue
            try:
                import json

                arr = json.loads(txt)
                if isinstance(arr, list):
                    total += len(arr)
            except Exception:
                continue
        return total

    def _is_first_author_bio_candidate(self, author_id: int) -> bool:
        existing = self.db.count("SELECT COUNT(*) FROM author_bios WHERE author_id=?", (author_id,))
        return existing == 0

    def _is_page_number_line(self, text: str) -> bool:
        t = text.strip()
        return t.isdigit() and 1 <= len(t) <= 3

    def _is_probable_unnumbered_title(self, lines: list[Line], idx: int) -> bool:
        t = lines[idx].text.strip()
        if not t:
            return False
        if idx > 0 and lines[idx - 1].text.strip():
            return False
        if self._is_page_number_line(t):
            return False
        if any(ch.isdigit() for ch in t):
            return False
        if self._is_term_header_line(t):
            return False
        if t.lstrip().startswith("[") or t.lstrip().startswith("【"):
            return False
        if any(ch in t for ch in _POETRY_PUNCT):
            return False
        if len(t) > 12:
            return False

        saw_poetry = False
        for j in range(idx + 1, min(len(lines), idx + 20)):
            tj = lines[j].text.strip()
            if not tj:
                continue
            cj = self.classifier.classify(tj)
            if cj.title is not None:
                return False
            if cj.looks_like_poetry:
                saw_poetry = True
                break
            if len(tj) > self.options.poetry_max_len and not cj.is_annotation_start and not cj.is_appendix_start:
                return False
        return saw_poetry

    def _is_probable_inline_title(self, lines: list[Line], idx: int) -> bool:
        t = lines[idx].text.strip()
        if not t:
            return False
        if self._is_page_number_line(t):
            return False
        if any(ch.isdigit() for ch in t):
            return False
        if self._is_term_header_line(t):
            return False
        if self._is_title_parenthetical(t):
            return False
        if t.lstrip().startswith("[") or t.lstrip().startswith("【"):
            return False
        if any(ch in t for ch in _POETRY_PUNCT):
            return False
        if len(t) > 12:
            return False

        saw_poetry = False
        for j in range(idx + 1, min(len(lines), idx + 12)):
            tj = lines[j].text.strip()
            if not tj:
                continue
            cj = self.classifier.classify(tj)
            if cj.title is not None:
                return False
            if cj.looks_like_poetry:
                saw_poetry = True
                break
            if cj.is_annotation_start or cj.is_appendix_start:
                return False
        return saw_poetry

    def _looks_like_author_line(self, lines: list[Line], idx: int) -> bool:
        t = lines[idx].text.strip()
        if not t:
            return False
        if self._is_page_number_line(t):
            return False
        if any(ch.isdigit() for ch in t):
            return False
        if t.lstrip().startswith("[") or t.lstrip().startswith("【"):
            return False
        if any(ch in t for ch in _POETRY_PUNCT):
            return False
        if len(t) > 12:
            return False

        for j in range(idx + 1, min(len(lines), idx + 6)):
            tj = lines[j].text.strip()
            if not tj:
                continue
            cj = self.classifier.classify(tj)
            if any(ch.isdigit() for ch in tj) and ("年" in tj or "字" in tj or "人" in tj or "（" in tj or "）" in tj):
                return True
            if ("（" in tj and "）" in tj) and ("年" in tj or "字" in tj or "人" in tj or "本名" in tj):
                return True
            if len(tj) > self.options.poetry_max_len and (any(ch.isdigit() for ch in tj) or "（" in tj or "）" in tj):
                return True
            if cj.looks_like_poetry:
                return False
            if len(tj) > self.options.poetry_max_len:
                return True
            return False
        return False

    def _looks_like_author_section_heading(self, lines: list[Line], idx: int) -> bool:
        raw = lines[idx].text.strip()
        if not raw:
            return False
        if self._is_page_number_line(raw):
            return False
        if any(ch.isdigit() for ch in raw):
            return False
        if raw.lstrip().startswith("[") or raw.lstrip().startswith("【"):
            return False
        if any(ch in raw for ch in _POETRY_PUNCT):
            return False

        name = raw.replace(" ", "")
        if not (2 <= len(name) <= 4):
            return False
        if not all("\u4e00" <= ch <= "\u9fff" for ch in name):
            return False

        for j in range(idx + 1, min(len(lines), idx + 6)):
            tj_raw = lines[j].text.strip()
            if not tj_raw:
                continue
            tj = tj_raw.replace(" ", "")
            if tj.startswith(name):
                if "字" in tj or "号" in tj or "本名" in tj or "生卒" in tj or "卒年" in tj:
                    return True
                if "（" in tj or "(" in tj or "—" in tj or "？" in tj or "?" in tj:
                    return True
                if "人" in tj or "今" in tj:
                    return True
                return False
            strong_hints = ("生卒", "卒年", "卒于", "生于", "本名", "父", "母", "姊", "卒")
            if any(h in tj for h in strong_hints):
                return True
            return False
        return False

    def _is_term_header_line(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if _TERM_HEADER_RE.match(t):
            return True
        if any(ch.isdigit() for ch in t) and ("学期" in t or "课程" in t):
            return True
        return False

    def _is_title_parenthetical(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if len(t) > 40:
            return False
        if (t.startswith("（") and t.endswith("）")) or (t.startswith("(") and t.endswith(")")):
            return True
        return False

    def _is_author_section_heading(self, lines: list[Line], idx: int) -> bool:
        if idx > 0 and lines[idx - 1].page_no == lines[idx].page_no and lines[idx - 1].text.strip():
            return False
        return self._looks_like_author_section_heading(lines, idx)
