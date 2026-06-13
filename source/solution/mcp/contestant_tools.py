from __future__ import annotations

import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from source.runtime.tool_context import resolve_readable, workspace_root


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def register_tools(*, register_tool: Callable[..., Callable], object_schema: Callable[..., dict]) -> None:
    """Register contestant MCP-style tools.

    These are the deterministic capabilities the main agent and skills lean on:
    listing/reading question files (including files extracted into the scratch
    workspace), real HTTP calls to the local services, SQLite reads, archive
    extraction, a multimodal vision bridge, and short local command execution.
    All file paths are validated against the question's declared files or the
    scratch workspace.
    """

    # ------------------------------------------------------------------ files
    @register_tool(
        name="list_dir",
        description=(
            "Recursively list files under an allowed question directory (or the "
            "scratch workspace). Use this to discover attachment files before "
            "reading them. Returns a JSON list of {path, size} entries."
        ),
        input_schema=object_schema(
            {
                "path": {
                    "type": "string",
                    "description": "Allowed directory path (relative to the question dir, or absolute).",
                },
                "max_entries": {"type": "integer", "description": "Max entries to return.", "default": 500},
            },
            ["path"],
        ),
        risk="low",
    )
    def list_dir(path: str, max_entries: int = 500) -> str:
        root = resolve_readable(path)
        if root.is_file():
            return _json([{"path": str(root), "size": root.stat().st_size}])
        entries: List[Dict[str, Any]] = []
        for item in sorted(root.rglob("*")):
            if item.is_file():
                entries.append({"path": str(item), "size": item.stat().st_size})
            if len(entries) >= max_entries:
                break
        return _json(entries)

    @register_tool(
        name="read_text_file",
        description=(
            "Read an allowed UTF-8 text file (question file OR a file extracted "
            "into the scratch workspace) and return its content. Use this for "
            "files produced by extract_archive."
        ),
        input_schema=object_schema(
            {
                "path": {"type": "string", "description": "File path to read."},
                "max_chars": {"type": "integer", "description": "Max characters.", "default": 64000},
            },
            ["path"],
        ),
        risk="medium",
    )
    def read_text_file(path: str, max_chars: int = 64000) -> str:
        target = resolve_readable(path)
        data = target.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        if max_chars and len(text) > max_chars:
            return text[:max_chars] + "\n[truncated]"
        return text

    # --------------------------------------------------------------- archives
    @register_tool(
        name="extract_archive",
        description=(
            "Extract a .zip/.tar archive (recursively, including nested archives) "
            "into the scratch workspace and return the list of extracted files. "
            "Read extracted text files with read_text_file and image files with "
            "vision_query."
        ),
        input_schema=object_schema(
            {"path": {"type": "string", "description": "Path to the archive file."}},
            ["path"],
        ),
        risk="medium",
    )
    def extract_archive(path: str) -> str:
        from source.solution.lib.archives import extract_all, walk_files

        archive = resolve_readable(path)
        dest = workspace_root() / (archive.stem + "__x")
        extract_all(archive, dest)
        files = [str(p) for p in walk_files(dest)]
        return _json({"extracted_root": str(dest), "file_count": len(files), "files": files})

    # ------------------------------------------------------------------- http
    @register_tool(
        name="http_request",
        description=(
            "Make a real HTTP request to a local service (e.g. the test API or "
            "Wiki service). Returns {status, headers, body}. Provide json_body "
            "for JSON requests; headers is an object of header name/value."
        ),
        input_schema=object_schema(
            {
                "method": {"type": "string", "description": "GET/POST/PUT/DELETE/..."},
                "url": {"type": "string", "description": "Full URL including host and path."},
                "headers": {"type": "object", "description": "Request headers.", "additionalProperties": True},
                "json_body": {"type": "object", "description": "JSON body object.", "additionalProperties": True},
                "body": {"type": "string", "description": "Raw text body (if not JSON)."},
                "timeout": {"type": "integer", "description": "Timeout seconds.", "default": 30},
            },
            ["method", "url"],
        ),
        risk="medium",
    )
    def http_request(
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        body: Optional[str] = None,
        timeout: int = 30,
    ) -> str:
        hdrs = dict(headers or {})
        data: Optional[bytes] = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = body.encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return _json({"status": resp.status, "headers": dict(resp.headers), "body": raw})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return _json({"status": exc.code, "headers": dict(exc.headers or {}), "body": raw})
        except Exception as exc:  # noqa: BLE001
            return _json({"status": -1, "error": str(exc)})

    # ----------------------------------------------------------------- sqlite
    @register_tool(
        name="sqlite_query",
        description=(
            "Run a read-only SQL query against an allowed SQLite database file and "
            "return rows as JSON. Use for .db files such as chat_history.db."
        ),
        input_schema=object_schema(
            {
                "db_path": {"type": "string", "description": "Path to the .db file."},
                "sql": {"type": "string", "description": "A single SELECT/PRAGMA/WITH statement."},
                "params": {"type": "array", "description": "Optional positional params.", "items": {}},
                "max_rows": {"type": "integer", "description": "Max rows.", "default": 1000},
            },
            ["db_path", "sql"],
        ),
        risk="low",
    )
    def sqlite_query(db_path: str, sql: str, params: Optional[List[Any]] = None, max_rows: int = 1000) -> str:
        target = resolve_readable(db_path)
        lowered = sql.strip().lower()
        if not (lowered.startswith("select") or lowered.startswith("pragma") or lowered.startswith("with")):
            return _json({"error": "only read-only SELECT/PRAGMA/WITH queries are allowed"})
        conn = sqlite3.connect("file:%s?mode=ro" % target, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, tuple(params or []))
            rows = [dict(r) for r in cur.fetchmany(max_rows)]
            return _json({"row_count": len(rows), "rows": rows})
        finally:
            conn.close()

    # ----------------------------------------------------------------- vision
    @register_tool(
        name="vision_query",
        description=(
            "Ask the multimodal model a question about one or more local images "
            "(question files or files extracted into the workspace). Returns the "
            "model's text answer. Use for screenshots, photos and image OCR."
        ),
        input_schema=object_schema(
            {
                "image_paths": {"type": "array", "description": "Image file paths.", "items": {"type": "string"}},
                "prompt": {"type": "string", "description": "What to extract/decide about the image(s)."},
            },
            ["image_paths", "prompt"],
        ),
        risk="medium",
    )
    def vision_query(image_paths: List[str], prompt: str) -> str:
        from source.solution.lib.llm import ask_with_images

        resolved = [resolve_readable(p) for p in image_paths]
        return ask_with_images(prompt, resolved, max_tokens=1500)

    # ------------------------------------------------------------------- exec
    @register_tool(
        name="run_command",
        description=(
            "Run a short local shell command (e.g. 'java -version', 'javac', "
            "'node'). Returns {returncode, stdout, stderr}. Use for compiling and "
            "running code the question provides. Provide stdin_text to feed the "
            "process standard input."
        ),
        input_schema=object_schema(
            {
                "command": {
                    "type": "array",
                    "description": "Argv list, e.g. [\"java\",\"-version\"].",
                    "items": {"type": "string"},
                },
                "stdin_text": {"type": "string", "description": "Optional stdin content."},
                "cwd": {"type": "string", "description": "Optional working directory (allowed path or workspace)."},
                "timeout": {"type": "integer", "description": "Timeout seconds.", "default": 60},
            },
            ["command"],
        ),
        risk="high",
    )
    def run_command(command: List[str], stdin_text: str = "", cwd: Optional[str] = None, timeout: int = 60) -> str:
        work = str(resolve_readable(cwd)) if cwd else str(workspace_root())
        try:
            completed = subprocess.run(
                [str(c) for c in command],
                input=stdin_text,
                text=True,
                capture_output=True,
                cwd=work,
                timeout=timeout,
                check=False,
            )
            return _json(
                {
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-12000:],
                    "stderr": completed.stderr[-6000:],
                }
            )
        except Exception as exc:  # noqa: BLE001
            return _json({"returncode": -1, "error": str(exc)})
