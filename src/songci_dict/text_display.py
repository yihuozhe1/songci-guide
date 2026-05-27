from __future__ import annotations


_HARD_LINE_END = set("，。？！；：、」』”’）)】]…")


def merge_soft_wraps(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue

        if not out:
            out.append(line)
            continue

        prev = out[-1]
        if prev == "":
            out.append(line)
            continue

        if line.startswith("【") or line.startswith("["):
            out.append(line)
            continue

        if _ends_hard(prev):
            out.append(line)
            continue

        if _should_merge(prev, line):
            out[-1] = prev + line
        else:
            out.append(line)

    return "\n".join(out).strip()


def _ends_hard(s: str) -> bool:
    if not s:
        return False
    last = s[-1]
    return last in _HARD_LINE_END


def _should_merge(prev: str, cur: str) -> bool:
    if any(ch.isdigit() for ch in prev):
        return False
    if len(prev) < 12:
        return False
    if len(cur) < 6:
        return False
    return True
