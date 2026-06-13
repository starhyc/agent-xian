from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from source.runtime.env_config import ModelConfig, load_dotenv
from source.runtime.tool_context import package_id


"""Synchronous model-gateway helper for contestant tools and skill scripts.

Skill scripts run as standalone subprocesses, so they cannot reach the main
agent's async client. They call into here instead. It is deliberately tiny and
dependency-free (urllib only), supports text and multimodal (image) messages,
retries transient failures, and always forwards the package_id header so the
platform can account token usage.
"""


class LLMError(RuntimeError):
    pass


def _endpoint(cfg: ModelConfig) -> str:
    return cfg.chat_completions_url


def _headers(cfg: ModelConfig) -> Dict[str, str]:
    headers = {
        "Authorization": "Bearer " + cfg.api_key,
        "Content-Type": "application/json",
    }
    pid = package_id() or cfg.package_id
    if pid:
        # Forward under both header spellings used across the contest material.
        headers["package_id"] = pid
        headers["X-Package-Id"] = pid
    return headers


def _extract_text(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("model returned no choices: " + json.dumps(data)[:300])
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        content = "".join(parts)
    return str(content or "").strip()


def chat(
    messages: List[Dict[str, Any]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    enable_thinking: bool = False,
    retries: int = 4,
    timeout: int = 120,
) -> str:
    """Call the gateway with OpenAI-style messages and return assistant text."""

    load_dotenv()
    cfg = ModelConfig.from_env()
    if not cfg.is_configured():
        raise LLMError("model gateway is not configured (check .env)")

    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                _endpoint(cfg), data=body, headers=_headers(cfg), method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = _parse(raw)
            return _extract_text(data)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_err = LLMError("HTTP %s: %s" % (exc.code, detail[:300]))
        except Exception as exc:  # noqa: BLE001 - retry everything transient
            last_err = exc
        time.sleep(min(2 ** attempt, 8))
    raise LLMError("model gateway failed after retries: %s" % last_err)


def _parse(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Tolerate SSE-style framing.
        text_parts: List[str] = []
        merged: Dict[str, Any] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            merged = obj
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                if isinstance(delta.get("content"), str):
                    text_parts.append(delta["content"])
        if merged:
            merged.setdefault("choices", [{}])
            merged["choices"][0]["message"] = {"content": "".join(text_parts)}
            return merged
        raise LLMError("non-JSON gateway response: " + raw[:300])


def ask(prompt: str, *, system: Optional[str] = None, **kwargs: Any) -> str:
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **kwargs)


def _image_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:%s;base64,%s" % (mime, b64)


def ask_with_images(
    prompt: str,
    image_paths: List[Path],
    *,
    system: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Multimodal call: a text prompt plus one or more local images."""

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in image_paths:
        content.append(
            {"type": "image_url", "image_url": {"url": _image_data_url(Path(img))}}
        )
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    return chat(messages, **kwargs)
