from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional


"""Helpers a skill *subprocess* uses to find the question's files.

Skill scripts run as standalone subprocesses (cwd = skill dir), so they cannot
read the in-process runtime context. ``SkillRuntime.run_skill`` exports the
question directory and the declared file allowlist as environment variables;
this module reads them back and resolves/validates paths.
"""


def _ensure_repo_on_path() -> None:
    # .../source/solution/lib/skillio.py -> repo root is parents[3] of source dir
    here = Path(__file__).resolve()
    # parents: lib(0) solution(1) source(2) repo_root(3)
    repo_root = here.parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_on_path()


def question_dir() -> Path:
    raw = os.getenv("QUESTION_DIR", "").strip()
    return Path(raw).resolve() if raw else Path.cwd().resolve()


def question_text() -> str:
    return os.getenv("QUESTION_TEXT", "")


def question_id() -> str:
    return os.getenv("QUESTION_ID", "")


def read_stdin_args() -> dict:
    """Parse the JSON arguments piped to a skill on stdin (may be empty)."""

    import json

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def clean_answer(text: str) -> str:
    """Strip thinking blocks / code fences the model may wrap around an answer."""

    import re

    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    cleaned = cleaned.strip()
    # Strip a single fenced block if the whole answer is fenced.
    fence = re.match(r"^```[a-zA-Z0-9_]*\s*(.*?)\s*```$", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    return cleaned


def emit(answer: str) -> None:
    """Print the final answer string for the skill (the only stdout output)."""

    print(clean_answer(answer))


def allowed_paths() -> List[Path]:
    raw = os.getenv("ALLOWED_FILE_PATHS", "").strip()
    if not raw:
        return []
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def resolve(path: str) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = (question_dir() / target).resolve()
    return target


def find_first(*names: str) -> Optional[Path]:
    """Return the first existing path among declared roots matching a name."""

    roots = allowed_paths() or [question_dir()]
    for root in roots:
        if root.is_file() and root.name in names:
            return root
        if root.is_dir():
            for name in names:
                hit = root / name
                if hit.exists():
                    return hit
    # Deep search as a fallback.
    for root in roots:
        base = root if root.is_dir() else root.parent
        for name in names:
            for hit in base.rglob(name):
                return hit
    return None


def list_files(suffixes: Optional[List[str]] = None) -> List[Path]:
    roots = allowed_paths() or [question_dir()]
    found: List[Path] = []
    seen = set()
    for root in roots:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = sorted(p for p in root.rglob("*") if p.is_file())
        else:
            files = []
        for f in files:
            if suffixes and f.suffix.lower() not in suffixes:
                continue
            if f in seen:
                continue
            seen.add(f)
            found.append(f)
    return found
