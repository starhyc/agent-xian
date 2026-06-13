from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


"""Lightweight debug logging for tool calls.

Every MCP tool invocation goes through ``LocalMCPClient.call_tool``; this module
gives that chokepoint a consistent, truncated, stderr-only log line so debugging
never pollutes stdout / results.json. Toggle with the ``AGENT_DEBUG_TOOLS`` env
var (default on); set it to 0/false/off to silence.
"""

_LOGGER_NAME = "agent.tools"
_MAX_FIELD = 600
_configured = False


def _enabled() -> bool:
    value = os.getenv("AGENT_DEBUG_TOOLS")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        _configured = True
    return logger


def _shorten(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_FIELD else value[:_MAX_FIELD] + "...<+%d chars>" % (len(value) - _MAX_FIELD)
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        out = [_shorten(v) for v in list(value)[:20]]
        if len(value) > 20:
            out.append("...<+%d items>" % (len(value) - 20))
        return out
    return value


def _fmt(obj: Any) -> str:
    try:
        return json.dumps(_shorten(obj), ensure_ascii=False, default=str)
    except Exception:
        return str(_shorten(obj))


def log_tool_start(name: str, args: dict, *, question_id: str = "") -> float:
    if _enabled():
        prefix = ("[q=%s] " % question_id) if question_id else ""
        _logger().debug("%s→ call %s args=%s", prefix, name, _fmt(args))
    return time.monotonic()


def log_tool_end(name: str, result: Any, started: float, *, question_id: str = "") -> None:
    if not _enabled():
        return
    elapsed_ms = int((time.monotonic() - started) * 1000)
    prefix = ("[q=%s] " % question_id) if question_id else ""
    size = len(result) if isinstance(result, (str, bytes, list, dict)) else "-"
    preview = result if isinstance(result, str) else _fmt(result)
    _logger().debug("%s✓ done %s in %dms size=%s result=%s", prefix, name, elapsed_ms, size, _shorten(preview))


def log_tool_error(name: str, exc: Exception, started: float, *, question_id: str = "") -> None:
    if not _enabled():
        return
    elapsed_ms = int((time.monotonic() - started) * 1000)
    prefix = ("[q=%s] " % question_id) if question_id else ""
    _logger().debug("%s✗ fail %s in %dms error=%s: %s", prefix, name, elapsed_ms, type(exc).__name__, exc)
