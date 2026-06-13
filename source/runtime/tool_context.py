from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


"""In-process runtime context shared with contestant tools and skills.

The contest runner only auto-resolves file paths for ``text_read_file``. Every
other tool / skill receives plain JSON arguments and has no idea where the
question files live. To let contestant tools and skill scripts read the
question's declared files (and files we extract from archives), the local MCP
client publishes the current runtime context here right before each tool call.

Nothing in this module talks to the network or the model; it is pure local
state plus path validation helpers.
"""


_CURRENT: Dict[str, Any] = {}

# Root under which we unpack archives / write scratch files. Reads from this
# directory are also allowed, so tools can read what extract_archive produced.
WORKSPACE_ROOT = Path(tempfile.gettempdir()) / "agent_xian_workspace"


def set_runtime_context(ctx: Optional[Dict[str, Any]]) -> None:
    _CURRENT.clear()
    if ctx:
        _CURRENT.update(ctx)


def get_runtime_context() -> Dict[str, Any]:
    return dict(_CURRENT)


def question_dir() -> Path:
    raw = _CURRENT.get("question_dir")
    return Path(raw).resolve() if raw else Path.cwd().resolve()


def question_text() -> str:
    return str(_CURRENT.get("question_text") or "")


def question_id() -> str:
    return str(_CURRENT.get("question_id") or "")


def allowed_paths() -> List[Path]:
    return [Path(p).resolve() for p in _CURRENT.get("allowed_file_paths", [])]


def package_id() -> str:
    return (os.getenv("PACKAGE_ID") or os.getenv("packageId") or "").strip()


def workspace_root() -> Path:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT


def _is_within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_readable(raw_path: str) -> Path:
    """Resolve a path the contestant tools are allowed to read.

    Accepts:
      * paths under any declared question file/dir (relative to question_dir),
      * absolute/relative paths that land inside the scratch workspace.

    Raises PermissionError if the path escapes both allowlists.
    """

    target = Path(raw_path)
    if not target.is_absolute():
        # Try question_dir first, then the workspace.
        candidate = (question_dir() / target).resolve()
        if _path_allowed(candidate):
            _require_exists(candidate)
            return candidate
        candidate = (workspace_root() / target).resolve()
        if _is_within(candidate, workspace_root().resolve()):
            _require_exists(candidate)
            return candidate
        # Fall back to question_dir resolution for a clearer error.
        target = candidate
    else:
        target = target.resolve()

    if not _path_allowed(target):
        raise PermissionError(f"path is not readable for this question: {raw_path}")
    _require_exists(target)
    return target


def _path_allowed(target: Path) -> bool:
    if _is_within(target, workspace_root().resolve()):
        return True
    for allowed in allowed_paths():
        if allowed.is_file() and target == allowed:
            return True
        if allowed.is_dir() and (target == allowed or _is_within(target, allowed)):
            return True
        # allowed may not exist yet / be a dir declared without trailing slash
        if not allowed.exists() and (target == allowed or _is_within(target, allowed)):
            return True
    return False


def _require_exists(target: Path) -> None:
    if not target.exists():
        raise FileNotFoundError(str(target))
