from __future__ import annotations

import hashlib
import html
import json
import os
import re
import ssl
import sys
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from pathlib import Path
from typing import Any

import streamlit as st

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from songci_dict.db import SongciDb
from songci_dict.ai_insight import build_messages_for_poem
from songci_dict.text_display import merge_soft_wraps
from songci_dict.zhipu import chat_completions
from songci_dict.db_router import resolve_db_target


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = APP_DIR / "data" / "songci.sqlite"
SCHEMA_PATH = APP_DIR / "schema.sql"
SCHEMA_POSTGRES_PATH = APP_DIR / "schema_postgres.sql"


def _clean_moedict_text(text: str) -> str:
    return " ".join(text.replace("`", "").replace("~", "").split())

def _shorten(text: str, *, limit: int) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    if len(t) <= limit:
        return t
    return f"{t[:limit].rstrip()}..."


@st.cache_data(show_spinner=False, ttl=86400)
def _fetch_moedict_entry(ch: str, *, verify_tls: bool) -> dict[str, str]:
    url = f"https://www.moedict.tw/a/{urlparse.quote(ch)}.json"
    req = urlrequest.Request(url, headers={"User-Agent": "songci-dict/1.0"})
    ctx = None if verify_tls else ssl._create_unverified_context()
    try:
        with urlrequest.urlopen(req, timeout=6, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as e:
        raw = b""
        try:
            raw = e.read() or b""
        except Exception:
            raw = b""
        payload_err: dict[str, Any] | None = None
        try:
            payload_err = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            payload_err = None
        if int(getattr(e, "code", 0) or 0) == 404:
            msg = ""
            if isinstance(payload_err, dict):
                msg = str(payload_err.get("message") or "").strip()
            return {
                "status": "not_found",
                "pinyin": "（无）",
                "definition": "（未收录）",
                "detail": msg,
            }
        return {"status": "error", "pinyin": "（无）", "definition": "（在线查询失败）", "detail": f"HTTP {getattr(e, 'code', '')}".strip()}
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return {
            "status": "error",
            "pinyin": "（无）",
            "definition": "（在线查询失败）",
            "detail": f"{type(e).__name__}: {e}".strip(),
        }

    if not isinstance(payload, dict):
        return {"status": "error", "pinyin": "（无）", "definition": "（返回格式异常）", "detail": "not a JSON object"}
    if payload.get("error"):
        return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": ""}
    hs = payload.get("h")
    if not isinstance(hs, list) or not hs:
        return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": ""}
    h0 = hs[0]
    if not isinstance(h0, dict):
        return {"status": "error", "pinyin": "（无）", "definition": "（词条结构异常）", "detail": ""}

    pinyin = str(h0.get("p") or h0.get("b") or "").strip()
    defs = h0.get("d")
    definition = ""
    if isinstance(defs, list):
        for d in defs:
            if isinstance(d, dict):
                definition = _clean_moedict_text(str(d.get("f") or "").strip())
                if definition:
                    break
    if not pinyin and not definition:
        return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": ""}
    if definition and len(definition) > 42:
        definition = f"{definition[:42].rstrip()}..."
    return {
        "status": "ok",
        "pinyin": pinyin or "（无）",
        "definition": definition or "（无）",
        "detail": "",
    }


@st.cache_data(show_spinner=False, ttl=86400)
def _fetch_zhwiktionary_entry(ch: str, *, verify_tls: bool) -> dict[str, str]:
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "titles": ch,
    }
    url = f"https://zh.wiktionary.org/w/api.php?{urlparse.urlencode(params)}"
    req = urlrequest.Request(url, headers={"User-Agent": "songci-dict/1.0"})
    ctx = None if verify_tls else ssl._create_unverified_context()
    try:
        with urlrequest.urlopen(req, timeout=8, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as e:
        return {"status": "error", "pinyin": "（无）", "definition": "（在线查询失败）", "detail": f"HTTP {getattr(e, 'code', '')}".strip()}
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return {"status": "error", "pinyin": "（无）", "definition": "（在线查询失败）", "detail": f"{type(e).__name__}: {e}".strip()}

    pages = None
    if isinstance(payload, dict):
        pages = payload.get("query", {}).get("pages")
    if not isinstance(pages, dict) or not pages:
        return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": ""}
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": ""}
    extract = str(page.get("extract") or "")
    if not extract.strip():
        return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": ""}

    lines = [ln.strip() for ln in extract.splitlines()]

    def _find_section_start(header: str) -> int | None:
        for i, ln in enumerate(lines):
            if ln.strip() == header:
                return i
        return None

    han_idx1 = _find_section_start("== 漢語 ==")
    han_idx2 = _find_section_start("== 汉语 ==")
    if han_idx1 is not None:
        han_idx = han_idx1
    elif han_idx2 is not None:
        han_idx = han_idx2
    else:
        han_idx = 0

    def_idx = None
    for i in range(han_idx, len(lines)):
        if lines[i] == "==== 釋義 ====" or lines[i] == "==== 释义 ====":
            def_idx = i
            break
    definition = ""
    if def_idx is not None:
        for ln in lines[def_idx + 1 :]:
            if not ln:
                continue
            if ln.startswith("="):
                break
            if ln == ch:
                continue
            definition = _clean_moedict_text(ln)
            if definition:
                break
    definition = _shorten(definition, limit=42) or "（无）"

    pinyin = ""
    pron_idx = None
    for i in range(han_idx, len(lines)):
        if lines[i] == "==== 發音 ====" or lines[i] == "==== 发音 ====":
            pron_idx = i
            break
    if pron_idx is not None:
        blob: list[str] = []
        for ln in lines[pron_idx + 1 : pron_idx + 80]:
            if ln.startswith("="):
                break
            if ln:
                blob.append(ln)
        pron_text = " ".join(blob)
        m = re.search(r"(?:拼音|漢語拼音|汉语拼音)[：:]\s*([A-Za-züÜāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜńňǹḿ]+(?:\s+[A-Za-züÜāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜńňǹḿ]+)*)", pron_text)
        if m:
            pinyin = m.group(1).strip()
        else:
            m2 = re.search(r"\s([A-Za-züÜāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜńňǹḿ]+)\s", f" {pron_text} ")
            if m2:
                pinyin = m2.group(1).strip()
    pinyin = _shorten(pinyin, limit=24) or "（无）"

    return {"status": "ok", "pinyin": pinyin, "definition": definition, "detail": ""}


def _fetch_best_chinese_dict_entry(ch: str, *, verify_tls: bool) -> dict[str, str]:
    r1 = _fetch_moedict_entry(ch, verify_tls=verify_tls)
    if r1.get("status") == "ok":
        return {**r1, "source": "萌典"}
    r2 = _fetch_zhwiktionary_entry(ch, verify_tls=verify_tls)
    if r2.get("status") == "ok":
        return {**r2, "source": "维基词典"}
    if r1.get("status") == "error" and r2.get("status") == "not_found":
        return {**r1, "source": "萌典"}
    if r2.get("status") == "error" and r1.get("status") == "not_found":
        return {**r2, "source": "维基词典"}
    if r1.get("status") == "error" and r2.get("status") == "error":
        return {**r1, "source": "萌典"}
    return {"status": "not_found", "pinyin": "（无）", "definition": "（未收录）", "detail": "", "source": "—"}


def _init_db(target) -> None:
    key = f"{getattr(target, 'kind', '')}::{getattr(target, 'sqlite_path', '')}::{getattr(target, 'postgres_dsn', '')}"
    if st.session_state.get("_schema_inited_for") == key:
        return
    with target.open() as db:
        db.init_schema(str(target.schema_path))
    st.session_state["_schema_inited_for"] = key


def _db_call(target, fn):
    with target.open() as db:
        db.init_schema(str(target.schema_path))
        return fn(db)


def _normalize_poem_content(text: str) -> str:
    t = merge_soft_wraps(str(text or ""))
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"\s+([，。？！；：、…])", r"\1", t)
    t = re.sub(r"([（(【\[])\s+", r"\1", t)
    t = re.sub(r"\s+([）)】\]])", r"\1", t)
    lines = t.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i].strip()
        if not cur:
            if out and out[-1] != "":
                out.append("")
            i += 1
            continue

        if len(cur) <= 3 and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt:
                if out and out[-1] and not out[-1].endswith(tuple("，。？！；：、」』”’）)】]…")):
                    out[-1] = out[-1] + cur
                    i += 1
                    continue
                if not cur.endswith(tuple("，。？！；：、」』”’）)】]…")):
                    lines[i + 1] = cur + nxt
                    i += 1
                    continue

        if out and out[-1] and not out[-1].endswith(tuple("，。？！；：、」』”’）)】]…")) and cur and len(cur) <= 4:
            out[-1] = out[-1] + cur
        else:
            out.append(cur)
        i += 1

    return "\n".join(out).strip()


def _inject_global_styles() -> None:
    st.markdown(
        """
        <style>
          :root {
            --paper: var(--background-color);
            --paper-strong: var(--secondary-background-color);
            --ink: var(--text-color);
            --ink-soft: var(--text-color);
            --line: color-mix(in srgb, var(--text-color) 16%, transparent);
            --gold: var(--primary-color);
            --gold-soft: color-mix(in srgb, var(--primary-color) 18%, transparent);
            --shadow: 0 20px 60px color-mix(in srgb, var(--text-color) 10%, transparent);
          }
          .stApp {
            background: var(--paper);
            color: var(--text-color);
          }
          [data-testid="stToolbarActions"],
          [data-testid="stStatusWidget"],
          [data-testid="stMainMenu"],
          [data-testid="stDecoration"],
          .stDeployButton,
          footer {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
          }
          [data-testid="stHeader"] {
            background: transparent !important;
          }
          [data-testid="stToolbar"] {
            background: transparent !important;
          }
          .stApp,
          [data-testid="stSidebar"],
          input,
          textarea,
          button {
            font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
          }
          [data-testid="stAppViewContainer"] > .main {
            background: transparent;
          }
          .block-container {
            max-width: 1240px;
            padding-top: 2.2rem;
            padding-bottom: 2rem;
          }
          [data-testid="stSidebar"] {
            background: var(--paper-strong);
            border-right: 1px solid var(--line);
          }
          [data-testid="stSidebar"] > div,
          [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            background: var(--paper-strong);
          }
          [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            padding-top: 1rem;
          }
          [data-testid="stSidebar"] h1,
          [data-testid="stSidebar"] h2,
          [data-testid="stSidebar"] h3,
          [data-testid="stSidebar"] label,
          [data-testid="stSidebar"] p,
          [data-testid="stSidebar"] span {
            color: var(--ink);
          }
          .app-hero {
            padding: 1.5rem 1.7rem;
            margin: 0 0 1.1rem 0;
            border: 1px solid var(--line);
            border-radius: 24px;
            background: var(--paper-strong);
            box-shadow: var(--shadow);
          }
          .app-kicker {
            margin: 0 0 0.45rem 0;
            color: var(--gold);
            font-size: 0.9rem;
            font-weight: 700;
            letter-spacing: 0.14em;
            text-transform: uppercase;
          }
          .app-title {
            margin: 0;
            font-size: 2.1rem;
            font-weight: 800;
            letter-spacing: 0.02em;
            color: var(--ink);
          }
          .app-subtitle {
            margin: 0.7rem 0 0 0;
            font-size: 1rem;
            line-height: 1.75;
            color: var(--ink-soft);
          }
          .chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
            margin-top: 1rem;
          }
          .chip {
            display: inline-flex;
            align-items: center;
            padding: 0.42rem 0.86rem;
            border-radius: 999px;
            background: var(--paper);
            border: 1px solid var(--line);
            color: var(--text-color);
            opacity: 0.86;
            font-size: 0.92rem;
          }
          .songci-shell {
            padding: 1.8rem 2rem 1.65rem;
            margin-bottom: 1rem;
            border: 1px solid var(--line);
            border-radius: 28px;
            background: var(--paper-strong);
            box-shadow: var(--shadow);
          }
          .songci-header {
            display: flex;
            flex-wrap: wrap;
            flex-direction: column;
            justify-content: center;
            gap: 0.35rem;
            align-items: center;
            text-align: center;
            padding-bottom: 1rem;
            margin-bottom: 1.35rem;
            border-bottom: 1px solid var(--line);
          }
          .songci-title {
            margin: 0 0 0.35rem 0;
            font-size: 2rem;
            font-weight: 800;
            color: var(--ink);
            font-family: "STKaiti", "KaiTi", "Kaiti SC", "Noto Serif SC", "Songti SC", "STSong", serif;
          }
          .songci-author {
            margin: 0;
            font-size: 1rem;
            color: var(--ink-soft);
            font-family: "STKaiti", "KaiTi", "Kaiti SC", "Noto Serif SC", "Songti SC", "STSong", serif;
            letter-spacing: 0.08em;
          }
          .songci-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            justify-content: center;
            width: 100%;
            margin-top: 0.55rem;
          }
          .songci-badge {
            padding: 0.42rem 0.76rem;
            border-radius: 999px;
            background: var(--gold-soft);
            color: var(--text-color);
            border: 1px solid var(--line);
            opacity: 0.88;
            font-size: 0.9rem;
            font-weight: 600;
          }
          .songci-body {
            max-width: 800px;
            margin: 0 auto;
            padding: 0 0.3rem;
          }
          .songci-content {
            font-family: "STKaiti", "KaiTi", "Kaiti SC", "Noto Serif SC", "Songti SC", "STSong", serif;
            color: var(--text-color);
            font-size: 1.2rem;
            line-height: 2.0;
            letter-spacing: 0.06em;
            white-space: pre-line !important;
            word-break: normal;
            overflow-wrap: break-word;
            text-align: justify;
            text-justify: inter-ideograph;
          }
          .panel-title {
            margin: 0 0 0.75rem 0;
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--ink);
          }
          .info-card {
            padding: 1rem 1.1rem;
            margin-bottom: 1rem;
            border-radius: 22px;
            border: 1px solid var(--line);
            background: var(--paper-strong);
            box-shadow: 0 12px 35px color-mix(in srgb, var(--text-color) 6%, transparent);
          }
          .fact-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.8rem;
          }
          .fact-label {
            font-size: 0.82rem;
            color: var(--ink-soft);
            margin-bottom: 0.22rem;
          }
          .fact-value {
            font-size: 0.98rem;
            color: var(--ink);
            font-weight: 600;
          }
          .notes-hint {
            margin: 0.25rem 0 0.9rem 0;
            color: var(--ink-soft);
            font-size: 0.92rem;
            line-height: 1.7;
          }
          .notes-title {
            margin: 0 0 0.3rem 0;
            font-size: 1.05rem;
            font-weight: 700;
            color: var(--text-color);
            opacity: 0.8;
            letter-spacing: 0.04em;
          }
          .notes-shell {
            padding: 1rem 1.1rem;
            border-radius: 22px;
            border: 1px solid var(--line);
            background: var(--paper-strong);
            box-shadow: 0 10px 30px color-mix(in srgb, var(--text-color) 5%, transparent);
          }
          .stButton > button,
          [data-testid="baseButton-secondary"] {
            border-radius: 999px;
            border: 1px solid var(--line);
            background: var(--paper-strong);
            color: var(--text-color);
            font-weight: 700;
            min-height: 2.65rem;
            padding: 0.2rem 1.1rem;
            box-shadow: 0 8px 18px color-mix(in srgb, var(--text-color) 6%, transparent);
          }
          .stButton > button:hover {
            border-color: var(--primary-color);
            color: var(--text-color);
          }
          .stTextInput input,
          .stTextArea textarea,
          .stSelectbox [data-baseweb="select"] > div,
          .stMultiSelect [data-baseweb="select"] > div {
            border-radius: 16px;
            border-color: var(--line);
            background: var(--paper);
          }
          .stSelectbox label,
          .stSelectbox div,
          .stSelectbox span {
            color: var(--text-color);
          }
          .stSelectbox [data-baseweb="select"] * {
            color: var(--text-color) !important;
          }
          div[role="listbox"] {
            background: var(--secondary-background-color) !important;
            color: var(--text-color) !important;
            border: 1px solid var(--line) !important;
          }
          div[role="listbox"] * {
            color: var(--text-color) !important;
          }
          .stTextArea textarea {
            line-height: 1.8;
          }
          [data-testid="stTabs"] {
            margin-top: 0.2rem;
          }
          [data-testid="stTabs"] [role="tablist"] {
            gap: 0.4rem;
            margin-bottom: 0.8rem;
          }
          [data-testid="stTabs"] [role="tab"] {
            border-radius: 999px;
            padding: 0.3rem 0.9rem;
            border: 1px solid var(--line);
            background: var(--paper-strong);
            color: var(--text-color);
          }
          [data-testid="stTabs"] [aria-selected="true"] {
            background: var(--paper);
            color: var(--text-color);
          }
          [data-testid="stExpander"] {
            border-radius: 18px;
            border: 1px solid var(--line);
            background: var(--paper-strong);
          }
          div[data-testid="stDataFrame"] {
            border-radius: 20px;
            overflow: hidden;
            border: 1px solid var(--line);
            box-shadow: 0 10px 30px color-mix(in srgb, var(--text-color) 5%, transparent);
          }
          @media (max-width: 900px) {
            .songci-shell {
              padding: 1.35rem 1.15rem;
            }
            .songci-title {
              font-size: 1.65rem;
            }
            .songci-content {
              font-size: 1.28rem;
            }
            .fact-grid {
              grid-template-columns: 1fr;
            }
            .songci-body {
              padding: 0 0.8rem;
            }
          }
          @media (min-width: 901px) {
            .songci-body {
              padding: 0 2.2rem;
            }
          }
          @media (max-width: 768px) {
            [data-testid="stSidebar"] {
              position: fixed !important;
              top: 0;
              left: 0;
              bottom: 0;
              height: 100vh;
              z-index: 9999;
              background: rgba(255, 255, 255, 0.98) !important;
              -webkit-backdrop-filter: none !important;
              backdrop-filter: none !important;
            }
            [data-testid="stSidebar"] > div,
            [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
              background: rgba(255, 255, 255, 0.98) !important;
            }
            [data-testid="stAppViewContainer"] > .main {
              position: relative;
              z-index: 0;
            }
            [data-testid="stAppViewContainer"] {
              overflow-x: hidden;
            }
            .songci-body {
              max-width: 100% !important;
              padding: 1rem 0.5rem !important;
            }
            .songci-content {
              text-align: left !important;
              text-justify: auto !important;
              font-size: 1.1rem !important;
              white-space: pre-line !important;
              word-break: normal !important;
              overflow-wrap: break-word !important;
            }
          }
          @media (prefers-color-scheme: dark) {
            :root {
              --background-color: #141211;
              --secondary-background-color: #1b1817;
              --text-color: #f2eadf;
              --primary-color: #d6b07a;
            }
          }
          @media (prefers-color-scheme: dark) and (max-width: 768px) {
            [data-testid="stSidebar"],
            [data-testid="stSidebar"] > div,
            [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
              background: rgba(27, 24, 23, 0.98) !important;
            }
          }
          .stTextInput input,
          .stTextArea textarea,
          [data-baseweb="input"] input {
            color: var(--text-color) !important;
            caret-color: var(--text-color) !important;
          }
          .stTextInput input::placeholder,
          .stTextArea textarea::placeholder,
          [data-baseweb="input"] input::placeholder {
            color: color-mix(in srgb, var(--text-color) 55%, transparent) !important;
          }
          [data-testid="stPopover"] button,
          button[data-testid="stPopoverButton"] {
            color: var(--text-color) !important;
            background: var(--secondary-background-color) !important;
            border: 1px solid var(--line) !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_app_hero(*, mode: str, query: str, required_only: bool) -> None:
    st.markdown(
        (
            "<section class='app-hero'>"
            "<h1 class='app-title'>宋词导读 · 数字辞典</h1>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_poem(poem: dict[str, Any]) -> None:
    title = str(poem["title"])
    author_name = str(poem["author_name"])
    content = _normalize_poem_content(str(poem["content"] or ""))

    st.markdown(
        (
            "<section class='songci-shell'>"
            "<div class='songci-header'>"
            f"<h2 class='songci-title'>{html.escape(title)}</h2>"
            f"<p class='songci-author'>{html.escape(author_name)}</p>"
            "</div>"
            f"<div class='songci-body'><div class='songci-content'>{html.escape(content)}</div></div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_poem_facts(*, poem: dict[str, Any], mode: str) -> None:
    seq_text = str(poem["seq_no"]) if poem["seq_no"] is not None else "未编号"
    required_text = "是" if int(poem["is_required"]) == 1 else "否"
    st.markdown("<div class='panel-title'>词作信息</div>", unsafe_allow_html=True)
    st.markdown(
        (
            "<section class='info-card'>"
            "<div class='fact-grid'>"
            "<div><div class='fact-label'>作者</div>"
            f"<div class='fact-value'>{html.escape(str(poem['author_name']))}</div></div>"
            "<div><div class='fact-label'>序号</div>"
            f"<div class='fact-value'>{html.escape(seq_text)}</div></div>"
            "<div><div class='fact-label'>是否必背</div>"
            f"<div class='fact-value'>{html.escape(required_text)}</div></div>"
            "<div><div class='fact-label'>浏览模式</div>"
            f"<div class='fact-value'>{html.escape(mode)}</div></div>"
            "</div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _annotations_view(annotations_text: str | None) -> None:
    if not annotations_text:
        st.write("（无注释）")
        return
    try:
        items = json.loads(annotations_text)
    except json.JSONDecodeError:
        st.text(annotations_text)
        return
    if not isinstance(items, list) or not items:
        st.write("（无注释）")
        return
    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            st.write(it)
            continue
        term = str(it.get("term") or "").strip()
        definition = str(it.get("definition") or "").strip()
        continuations = it.get("continuations") or []
        raw = str(it.get("raw") or "").strip()
        if term and definition:
            st.markdown(f"{idx}. {term}：{definition}")
        elif raw:
            st.markdown(f"{idx}. {raw}")
        else:
            st.markdown(f"{idx}. （空）")
        if isinstance(continuations, list):
            for ln in continuations:
                ln_s = str(ln).strip()
                if ln_s:
                    st.markdown(f"- {ln_s}")


def _author_bio_view(bios: list[Any]) -> None:
    if not bios:
        st.write("（无作者简介）")
        return
    for bio in bios:
        if isinstance(bio, dict):
            bio_text = str(bio.get("bio") or "")
            source_pdf = str(bio.get("source_pdf") or "")
            source_page = bio.get("source_page")
        elif hasattr(bio, "__getitem__"):
            bio_text = str(bio["bio"])
            source_pdf = str(bio["source_pdf"] or "")
            source_page = bio["source_page"]
        else:
            bio_text = str(bio)
            source_pdf = ""
            source_page = None
        st.markdown(bio_text)
        if source_pdf or source_page:
            st.caption(f"{source_pdf or ''} p.{source_page or ''}".strip())
        st.divider()


def _get_zhipu_api_key() -> str:
    try:
        v = str(st.secrets.get("ZHIPU_API_KEY", "") or "").strip()
    except Exception:
        v = ""
    if v:
        return v
    return str(os.getenv("ZHIPU_API_KEY") or "").strip()


def _get_zhipu_model() -> str:
    try:
        v = str(st.secrets.get("ZHIPU_MODEL", "") or "").strip()
    except Exception:
        v = ""
    return v or "glm-5.1"


def _rare_chars_panel() -> None:
    st.subheader("生僻字检索")
    text = st.text_input("输入文字", key="rare_chars_input")
    if not text:
        return

    cols = st.columns([1, 1, 2])
    with cols[0]:
        verify_tls = st.checkbox("验证证书", value=True, key="moedict_verify_tls")
    with cols[1]:
        if st.button("清除缓存", key="moedict_clear_cache"):
            _fetch_moedict_entry.clear()
            _fetch_zhwiktionary_entry.clear()
            st.rerun()
    with cols[2]:
        st.caption("在线词典：优先萌典，未收录再查维基词典；仅显示拼音与简短中文释义。")

    rows: list[dict[str, str]] = []
    cache: dict[str, dict[str, str]] = {}
    chars = [c for c in text if not c.isspace()]
    for ch in chars:
        if ch not in cache:
            cache[ch] = _fetch_best_chinese_dict_entry(ch, verify_tls=verify_tls)
        rec_view = cache[ch]
        status = rec_view.get("status", "error")
        status_text = "在线"
        if status == "not_found":
            status_text = "在线未收录"
        elif status == "error":
            status_text = "在线失败"
        rows.append(
            {
                "字": ch,
                "拼音": rec_view.get("pinyin", "（无）"),
                "释义": rec_view.get("definition", "（无）"),
                "来源": rec_view.get("source", "—"),
                "状态": status_text,
                "原因": rec_view.get("detail", ""),
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_rare_chars_entry() -> None:
    cols = st.columns([6, 1])
    with cols[1]:
        if hasattr(st, "popover"):
            with st.popover("字典"):
                _rare_chars_panel()
        else:
            with st.expander("字典", expanded=False):
                _rare_chars_panel()


st.set_page_config(
    page_title="宋词导读·数字辞典",
    layout="wide",
    initial_sidebar_state="expanded",
)
_inject_global_styles()

st.sidebar.title("宋词导读")
mode = st.sidebar.radio("模式", ["辞海模式", "挑战模式"], index=0)
required_only = st.sidebar.checkbox("仅必背", value=False)
query = st.sidebar.text_input("搜索（标题/作者/正文）", value="", key="search_query")
user_key = st.sidebar.text_input("user_key（用于笔记隔离）", value=st.session_state.get("user_key", ""))
st.session_state["user_key"] = user_key

try:
    secrets_db_mode = str(st.secrets.get("DB_MODE", "") or "").strip()
except Exception:
    secrets_db_mode = ""
try:
    secrets_pg_dsn = str(st.secrets.get("POSTGRES_DSN", "") or "").strip()
except Exception:
    secrets_pg_dsn = ""

target = resolve_db_target(
    db_mode=secrets_db_mode,
    sqlite_path=DEFAULT_DB_PATH,
    sqlite_schema_path=SCHEMA_PATH,
    postgres_schema_path=SCHEMA_POSTGRES_PATH,
    postgres_dsn=secrets_pg_dsn,
)

with st.sidebar:
    st.caption(f"DB：{target.kind}")

if target.kind == "sqlite":
    db_path = Path(str(target.sqlite_path or ""))
    if not db_path.exists():
        st.error(
            f"未找到数据库：{db_path}。请先运行 scripts/build_db.py --pdf \"宋词导读作品选（2026年春）.pdf\" --overwrite 构建，或配置 DB_MODE=postgres/auto + POSTGRES_DSN 使用云端数据库。"
        )
        st.stop()

_init_db(target)
_render_rare_chars_entry()
_render_app_hero(mode=mode, query=query, required_only=required_only)


def _pick_default_poem_id() -> int | None:
    poems = _db_call(
        target,
        lambda db: db.list_poems(query=query, required_only=required_only, limit=1),
    )
    if not poems:
        return None
    return int(poems[0]["id"])


if "current_poem_id" not in st.session_state:
    st.session_state["current_poem_id"] = _pick_default_poem_id()

if mode == "辞海模式":
    poems = _db_call(
        target,
        lambda db: db.list_poems(query=query, required_only=required_only, limit=500),
    )
    if not poems:
        st.warning("未找到匹配的词作。")
        st.stop()

    options = [int(r["id"]) for r in poems]
    labels: dict[int, str] = {}
    for r in poems:
        pid = int(r["id"])
        seq = r["seq_no"]
        star = "*" if int(r["is_required"]) == 1 else ""
        if seq is None:
            labels[pid] = f"{star}{r['title']} · {r['author_name']}"
        else:
            labels[pid] = f"{star}{seq}. {r['title']} · {r['author_name']}"

    if st.session_state.get("current_poem_id") not in options:
        st.session_state["current_poem_id"] = options[0]

    st.selectbox(
        "选择词作",
        options=options,
        key="current_poem_id",
        format_func=lambda pid: labels.get(int(pid), str(pid)),
    )
else:
    cols = st.columns([1, 3])
    with cols[0]:
        if st.button("随机抽取必背词"):
            pid = _db_call(target, lambda db: db.random_required_poem_id())
            st.session_state["current_poem_id"] = pid
        if st.session_state.get("current_poem_id") is None:
            st.info("点击按钮开始。")
    with cols[1]:
        st.write("挑战模式（Phase2 占位）：仅提供随机抽取必背词用于演示与自测。")

poem_id = st.session_state.get("current_poem_id")
if poem_id is None:
    st.stop()

poem = _db_call(target, lambda db: db.get_poem(int(poem_id)))
if poem is None:
    st.error("该词作不存在或已被删除。")
    st.stop()

_render_poem(dict(poem))

tabs = st.tabs(["作者简介", "题记/小序", "注释", "附录/赏析", "🤖 AI 深度解读"])

with tabs[0]:
    bios = _db_call(target, lambda db: db.list_author_bios(int(poem["author_id"])))
    _author_bio_view(bios)

with tabs[1]:
    headnote = poem["headnote"]
    if headnote:
        st.markdown(str(headnote))
    else:
        st.write("（无题记）")

with tabs[2]:
    _annotations_view(poem["annotations"])

with tabs[3]:
    appendix = poem["appendix"]
    if appendix:
        st.markdown(str(appendix))
    else:
        st.write("（无附录）")

with tabs[4]:
    insight = _db_call(target, lambda db: db.get_ai_insight(int(poem_id)))
    if insight and insight["content"]:
        st.markdown(str(insight["content"]))
        if insight["source"]:
            st.caption(str(insight["source"]))
    else:
        zkey = _get_zhipu_api_key()
        if zkey:
            model = _get_zhipu_model()
            if st.button("生成解读（在线）", key=f"gen_ai_insight_{poem_id}"):
                with st.spinner("正在生成 AI 深度解读..."):
                    def _gen(db: SongciDb) -> None:
                        messages = build_messages_for_poem(dict(poem), style="short300")
                        res = chat_completions(api_key=zkey, model=model, messages=messages, max_tokens=600)
                        db.upsert_ai_insight(poem_id=int(poem_id), content=res.content, source=f"zhipu:{model}")
                        db.commit()

                    try:
                        _db_call(target, _gen)
                        st.success("已生成并写入数据库。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"{type(e).__name__}: {e}")
            else:
                st.info("AI 深度解读暂缺（已配置 ZHIPU_API_KEY，可点击按钮在线生成并写入本地数据库）。")
        else:
            st.info("AI 深度解读暂缺（未配置 ZHIPU_API_KEY；本页面仅展示预存内容）。")

st.divider()

st.markdown("<div class='notes-title'>课堂笔记</div>", unsafe_allow_html=True)
st.markdown(
    "<p class='notes-hint'>填写同一 `user_key` 后，可在不同设备间共享笔记内容。</p>",
    unsafe_allow_html=True,
)

with st.container():
    uk = (user_key or "").strip()
    existing_note = None
    if uk:
        note_row = _db_call(target, lambda db: db.get_poem_note(int(poem_id), uk))
        existing_note = note_row["content"] if note_row else ""

    note_widget_key = "note_" + hashlib.sha256(f"{poem_id}::{uk}".encode("utf-8")).hexdigest()[:16]
    if note_widget_key not in st.session_state:
        st.session_state[note_widget_key] = str(existing_note or "")

    def _save_current_note() -> None:
        note_text = str(st.session_state.get(note_widget_key, ""))
        if not uk:
            st.warning("请先填写 user_key 后再保存笔记。")
            st.stop()

        def _save(db: SongciDb) -> None:
            db.upsert_poem_note(poem_id=int(poem_id), user_key=uk, content=note_text)
            db.commit()

        _db_call(target, _save)
        st.success("已保存。")

    with st.sidebar:
        if st.button("保存当前笔记", key=f"save_sidebar_{note_widget_key}"):
            _save_current_note()

    if st.button("保存笔记", key=f"save_{note_widget_key}"):
        _save_current_note()

    st.text_area(
        "写下你的课堂笔记（同一 user_key 可在手机/PC 共享）",
        height=170,
        key=note_widget_key,
    )

    if not uk:
        st.caption("填写 user_key 后可保存（用于多端同步与隔离）。")
