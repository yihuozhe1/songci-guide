from __future__ import annotations

import json
from typing import Any


def _fmt_block(title: str, content: str | None) -> str:
    t = (content or "").strip()
    if not t:
        return ""
    return f"{title}\n{t}\n"


def _fmt_annotations(annotations_text: str | None) -> str:
    if not annotations_text:
        return ""
    try:
        items = json.loads(annotations_text)
    except json.JSONDecodeError:
        return f"注释（原始文本）\n{annotations_text.strip()}\n"
    if not isinstance(items, list) or not items:
        return ""
    lines: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            s = str(it).strip()
            if s:
                lines.append(s)
            continue
        term = str(it.get("term") or "").strip()
        definition = str(it.get("definition") or "").strip()
        raw = str(it.get("raw") or "").strip()
        if term and definition:
            lines.append(f"- {term}：{definition}")
        elif raw:
            lines.append(f"- {raw}")
    if not lines:
        return ""
    return "注释（节选）\n" + "\n".join(lines) + "\n"


def build_messages_for_poem(poem: dict[str, Any], *, style: str = "teaching") -> list[dict[str, str]]:
    title = str(poem.get("title") or "").strip()
    author = str(poem.get("author_name") or "").strip()
    content = str(poem.get("content") or "").strip()
    headnote = poem.get("headnote")
    appendix = poem.get("appendix")
    annotations = poem.get("annotations")

    sys = "你是一位宋词导师，擅长用通俗但准确的中文做文本细读与赏析。"
    if style == "exam":
        sys = "你是一位宋词老师，面向考试与课堂讲解，输出要点清晰、便于背诵。"
    elif style == "short300":
        sys = "你是一位宋词导读老师，擅长用精炼中文写课堂式赏析。"

    src = ""
    src += _fmt_block("题目", title)
    src += _fmt_block("作者", author)
    src += _fmt_block("题记/小序", str(headnote) if headnote is not None else "")
    src += _fmt_block("正文", content)
    src += _fmt_annotations(str(annotations) if annotations is not None else None)
    src += _fmt_block("附录/赏析（若有）", str(appendix) if appendix is not None else "")
    src = src.strip()

    if style == "short300":
        user = (
            "请基于下面提供的原文材料生成约 300 字的赏析（AI 解读）。\n"
            "要求：\n"
            "1) 只依据材料，不要编造作者生平、创作背景、典故出处；不确定就写“（无法从原文确定）”。\n"
            "2) 输出为 Markdown，不要使用标题，不要列表，只写 1–2 段正文。\n"
            "3) 必须引用原文中的 1–2 处关键词/短句（用引号标出），并解释其意象/情感作用（可参考注释）。\n"
            "4) 字数控制在 240–360 个汉字之间。\n\n"
            f"{src}\n"
        )
    else:
        user = (
            "请基于下面提供的原文材料生成《AI 深度解读》。\n"
            "要求：\n"
            "1) 只依据材料，不要编造作者生平、创作背景、典故出处；不确定就写“（无法从原文确定）”。\n"
            "2) 输出为 Markdown，包含且仅包含以下四个一级标题：\n"
            "   - # 一句话主旨\n"
            "   - # 结构与情感线\n"
            "   - # 逐句细读（按行/分句）\n"
            "   - # 易错点与记忆钩子\n"
            "3) “逐句细读”中要尽量引用原句并解释关键意象/关键词（可参考注释）。\n"
            "4) 全文控制在 600–1200 字。\n\n"
            f"{src}\n"
        )

    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]
