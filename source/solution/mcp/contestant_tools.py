from __future__ import annotations

import base64
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests


def register_tools(*, register_tool: Callable[..., Callable], object_schema: Callable[..., dict[str, Any]]) -> None:
    """Register contestant MCP-style tools.

    This file is loaded by source/toolkits/main_mcp.py. Contestants can add,
    remove, or replace tools here when a capability is better exposed as a
    direct MCP-style function than as a SKILL.md package.
    """

    # === 原有的 Mock 工具 ===
    @register_tool(
        name="mock_order_lookup",
        description="Mock MCP-style tool. Returns a fixed mock order lookup result for demo purposes.",
        input_schema=object_schema(
            {
                "order_id": {
                    "type": "string",
                    "description": "Mock order id, for example MOCK-1001.",
                }
            },
            ["order_id"],
        ),
        kind="mcp",
        risk="low",
    )
    def mock_order_lookup(order_id: str) -> str:
        return json.dumps(
            {
                "mock_result": "mock-order-lookup-ok",
                "source": "mock_mcp",
                "order_id": order_id,
            },
            ensure_ascii=False,
            indent=2,
        )

    @register_tool(
        name="mock_policy_check",
        description="Mock MCP-style tool. Returns a fixed mock policy check result for demo purposes.",
        input_schema=object_schema(
            {
                "payload": {
                    "type": "string",
                    "description": "Mock payload to check.",
                }
            },
            ["payload"],
        ),
        kind="mcp",
        risk="low",
    )
    def mock_policy_check(payload: str) -> str:
        return json.dumps(
            {
                "mock_result": "mock-policy-check-ok",
                "source": "mock_mcp",
                "payload_preview": payload[:80],
            },
            ensure_ascii=False,
            indent=2,
        )

    # === 新增：文件处理工具 ===

    @register_tool(
        name="zip_extract",
        description="Extract a zip archive to a specified directory. Returns the list of extracted files.",
        input_schema=object_schema(
            {
                "zip_path": {
                    "type": "string",
                    "description": "Path to the zip file to extract.",
                },
                "extract_dir": {
                    "type": "string",
                    "description": "Directory to extract the zip contents to.",
                },
            },
            ["zip_path", "extract_dir"],
        ),
        kind="mcp",
        risk="medium",
    )
    def zip_extract(zip_path: str, extract_dir: str) -> str:
        """Extract zip file to specified directory."""
        zip_file = Path(zip_path)
        output_dir = Path(extract_dir)

        if not zip_file.exists():
            return json.dumps({"error": f"Zip file not found: {zip_path}"}, ensure_ascii=False)

        output_dir.mkdir(parents=True, exist_ok=True)
        extracted_files = []

        with zipfile.ZipFile(zip_file, "r") as zf:
            zf.extractall(output_dir)
            extracted_files = zf.namelist()

        return json.dumps(
            {
                "success": True,
                "extracted_count": len(extracted_files),
                "extracted_files": extracted_files,
                "extract_dir": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )

    @register_tool(
        name="image_to_base64",
        description="Convert an image file to base64 encoded string for use with multimodal models.",
        input_schema=object_schema(
            {
                "image_path": {
                    "type": "string",
                    "description": "Path to the image file to convert.",
                },
                "max_size_kb": {
                    "type": "integer",
                    "description": "Maximum file size in KB before compression warning.",
                    "default": 4096,
                },
            },
            ["image_path"],
        ),
        kind="mcp",
        risk="low",
    )
    def image_to_base64(image_path: str, max_size_kb: int = 4096) -> str:
        """Convert image to base64 string."""
        img_file = Path(image_path)

        if not img_file.exists():
            return json.dumps({"error": f"Image file not found: {image_path}"}, ensure_ascii=False)

        file_size_kb = img_file.stat().st_size // 1024
        if file_size_kb > max_size_kb:
            return json.dumps(
                {
                    "warning": f"Image size ({file_size_kb}KB) exceeds recommended limit ({max_size_kb}KB)",
                    "image_path": image_path,
                    "size_kb": file_size_kb,
                },
                ensure_ascii=False,
            )

        with img_file.open("rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")

        suffix = img_file.suffix.lower().lstrip(".")
        mime_type_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "bmp": "image/bmp",
            "webp": "image/webp",
        }
        mime_type = mime_type_map.get(suffix, "image/jpeg")

        return json.dumps(
            {
                "success": True,
                "base64": b64_data,
                "mime_type": mime_type,
                "size_kb": file_size_kb,
            },
            ensure_ascii=False,
        )

    @register_tool(
        name="http_request",
        description="Make an HTTP request to test APIs. Supports GET, POST, PUT, DELETE methods.",
        input_schema=object_schema(
            {
                "url": {
                    "type": "string",
                    "description": "Full URL to request.",
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method: GET, POST, PUT, DELETE.",
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP headers as key-value pairs.",
                },
                "json_body": {
                    "type": "object",
                    "description": "JSON body for POST/PUT requests.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds.",
                    "default": 30,
                },
            },
            ["url"],
        ),
        kind="mcp",
        risk="medium",
    )
    def http_request(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> str:
        """Make HTTP request to specified URL."""
        try:
            request_kwargs: dict[str, Any] = {
                "url": url,
                "method": method.upper(),
                "timeout": timeout,
            }
            if headers:
                request_kwargs["headers"] = headers
            if json_body and method.upper() in ("POST", "PUT"):
                request_kwargs["json"] = json_body

            response = requests.request(**request_kwargs)

            return json.dumps(
                {
                    "success": True,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text[:10000],  # Limit body size
                    "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
                },
                ensure_ascii=False,
                indent=2,
            )
        except requests.exceptions.Timeout:
            return json.dumps({"error": "Request timeout", "url": url}, ensure_ascii=False)
        except requests.exceptions.ConnectionError:
            return json.dumps({"error": "Connection failed", "url": url}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc), "url": url}, ensure_ascii=False)

    @register_tool(
        name="execute_java",
        description="Compile and execute a Java source file. Returns the stdout/stderr output.",
        input_schema=object_schema(
            {
                "java_file": {
                    "type": "string",
                    "description": "Path to the Java source file.",
                },
                "classpath": {
                    "type": "string",
                    "description": "Optional classpath for dependencies.",
                },
                "args": {
                    "type": "array",
                    "description": "Command line arguments to pass to the Java program.",
                    "items": {"type": "string"},
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds.",
                    "default": 60,
                },
            },
            ["java_file"],
        ),
        kind="mcp",
        risk="medium",
    )
    def execute_java(
        java_file: str,
        classpath: str | None = None,
        args: list[str] | None = None,
        timeout: int = 60,
    ) -> str:
        """Compile and run a Java source file."""
        java_path = Path(java_file)

        if not java_path.exists():
            return json.dumps({"error": f"Java file not found: {java_file}"}, ensure_ascii=False)

        # Determine the class name from the file
        content = java_path.read_text(encoding="utf-8")
        class_name = None
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("public class "):
                class_name = line.split()[2].split("{")[0].strip()
                break

        if not class_name:
            return json.dumps({"error": "Could not find public class in Java file"}, ensure_ascii=False)

        # Create temp directory for compilation output
        import tempfile

        temp_dir = Path(tempfile.mkdtemp())
        output_dir = temp_dir / "classes"
        output_dir.mkdir(exist_ok=True)

        try:
            # Compile
            compile_cmd = ["javac", "-d", str(output_dir)]
            if classpath:
                compile_cmd.extend(["-cp", classpath])
            compile_cmd.append(str(java_path))

            compile_result = subprocess.run(
                compile_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if compile_result.returncode != 0:
                return json.dumps(
                    {
                        "error": "Compilation failed",
                        "stdout": compile_result.stdout,
                        "stderr": compile_result.stderr,
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            # Run
            run_cmd = ["java", "-cp", str(output_dir), class_name]
            if args:
                run_cmd.extend(args)

            run_result = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return json.dumps(
                {
                    "success": True,
                    "return_code": run_result.returncode,
                    "stdout": run_result.stdout,
                    "stderr": run_result.stderr,
                    "class_name": class_name,
                },
                ensure_ascii=False,
                indent=2,
            )

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Execution timeout"}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        finally:
            # Cleanup
            import shutil

            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    # === 新增：文件搜索工具 ===

    @register_tool(
        name="search_in_files",
        description="Search for text pattern in files within a directory. Returns matching files and lines.",
        input_schema=object_schema(
            {
                "directory": {
                    "type": "string",
                    "description": "Directory to search in.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for.",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File glob pattern to match, e.g., *.md",
                    "default": "*",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether search should be case sensitive.",
                    "default": False,
                },
            },
            ["directory", "pattern"],
        ),
        kind="mcp",
        risk="low",
    )
    def search_in_files(
        directory: str,
        pattern: str,
        file_pattern: str = "*",
        case_sensitive: bool = False,
    ) -> str:
        """Search for pattern in files within a directory."""
        import fnmatch
        import re

        search_dir = Path(directory)
        if not search_dir.exists():
            return json.dumps({"error": f"Directory not found: {directory}"}, ensure_ascii=False)

        regex_flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled_pattern = re.compile(pattern, regex_flags)
        except re.error as exc:
            return json.dumps({"error": f"Invalid regex pattern: {exc}"}, ensure_ascii=False)

        matches = []
        for file_path in search_dir.rglob(file_pattern):
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for line_num, line in enumerate(content.split("\n"), start=1):
                    if compiled_pattern.search(line):
                        matches.append(
                            {
                                "file": str(file_path),
                                "line": line_num,
                                "content": line.strip()[:200],
                            }
                        )
            except Exception:
                continue

        return json.dumps(
            {
                "success": True,
                "pattern": pattern,
                "directory": str(search_dir),
                "match_count": len(matches),
                "matches": matches[:100],  # Limit results
            },
            ensure_ascii=False,
            indent=2,
        )