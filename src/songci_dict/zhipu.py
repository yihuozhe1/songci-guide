from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest


@dataclass(frozen=True, slots=True)
class ZhipuChatResult:
    content: str
    raw: dict[str, Any]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _jwt_hs256_token(*, api_key: str, exp_seconds: int) -> str:
    parts = api_key.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid Zhipu API key format")
    api_key_id, api_key_secret = parts[0].strip(), parts[1].strip()
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"api_key": api_key_id, "exp": now + int(exp_seconds), "timestamp": now}
    h = _b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = hmac.new(api_key_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def _auth_bearer(*, api_key: str) -> str:
    k = api_key.strip()
    if not k:
        raise ValueError("Missing Zhipu API key")
    mode = (os.getenv("ZHIPU_AUTH_MODE") or "raw").strip().lower()
    if mode == "jwt":
        return _jwt_hs256_token(api_key=k, exp_seconds=3600)
    return k


def chat_completions(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.6,
    max_tokens: int = 1200,
    thinking_type: str = "disabled",
    timeout_s: int = 45,
    endpoint: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
) -> ZhipuChatResult:
    body = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    tt = (thinking_type or "").strip().lower()
    if tt:
        body["thinking"] = {"type": tt}
    req = urlrequest.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_auth_bearer(api_key=api_key)}",
            "User-Agent": "songci-dict/1.0",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"Zhipu HTTP {getattr(e, 'code', '')}: {detail}".strip()) from e
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise RuntimeError(f"Zhipu request failed: {type(e).__name__}: {e}") from e

    if not isinstance(payload, dict):
        raise RuntimeError("Zhipu response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"Zhipu response missing choices: {payload}")
    ch0 = choices[0]
    if not isinstance(ch0, dict):
        raise RuntimeError(f"Zhipu response invalid choice: {payload}")
    msg = ch0.get("message")
    content = ""
    reasoning = ""
    if isinstance(msg, dict):
        content = str(msg.get("content") or "").strip()
        reasoning = str(msg.get("reasoning_content") or "").strip()
    if not content:
        if reasoning:
            finish = str(ch0.get("finish_reason") or "")
            usage = payload.get("usage")
            raise RuntimeError(
                "Zhipu response has no content (only reasoning_content). "
                f"finish_reason={finish} usage={usage}. "
                "Try thinking_type='disabled' or increase max_tokens."
            )
        raise RuntimeError(f"Zhipu response empty content: {payload}")
    return ZhipuChatResult(content=content, raw=payload)
