from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log(msg: str) -> None:
    print(f"[sensitive_scan] {msg}", file=sys.stderr)


# 默认敏感信息类型
DEFAULT_TYPES = ["phone", "email", "id_card", "api_key"]
MAX_DEPTH = 5
MAX_FILE_SIZE = 20 * 1024 * 1024

# 敏感信息正则模式
QUESTION_PATTERNS = {
    "phone": [
        (r"\b1\d{10}\b", "Phone Number (1开头11位)"),
    ],
    "email": [
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "Email Address"),
    ],
    "id_card": [
        (r"\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "ID Card (18位)"),
    ],
    "api_key": [
        (r"sk-[A-Za-z0-9_-]+", "API Key (sk-开头)"),
    ],
}

# 支持的图片格式
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}

ARCHIVE_EXTENSIONS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")


def init_counts(types: list[str]) -> dict[str, int]:
    return {t: 0 for t in types}


def scan_text_content(content: str, patterns: dict[str, list], types: list[str]) -> dict[str, int]:
    """扫描文本内容中的敏感信息"""
    counts = init_counts(types)
    
    for category, pattern_list in patterns.items():
        if category not in counts:
            continue
        for pattern, _ in pattern_list:
            matches = re.findall(pattern, content, re.IGNORECASE)
            counts[category] += len(matches)
    
    return counts


def scan_file_text(file_path: Path, patterns: dict[str, list], types: list[str]) -> dict[str, int]:
    """扫描文本文件"""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return scan_text_content(content, patterns, types)
    except Exception:
        return init_counts(types)


def extract_tar(tar_data: bytes) -> list[tuple[str, bytes]]:
    """解压 tar 文件，返回文件列表 (文件名, 内容)"""
    files = []
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:*") as tf:
            for member in tf.getmembers():
                if member.isfile() and member.size <= MAX_FILE_SIZE:
                    f = tf.extractfile(member)
                    if f:
                        files.append((member.name, f.read()))
    except Exception:
        pass
    return files


def extract_nested_archive(
    name: str,
    data: bytes,
    patterns: dict[str, list],
    counts: dict[str, int],
    types: list[str],
    depth: int = 0,
) -> None:
    """递归处理嵌套压缩包"""
    if depth > MAX_DEPTH:
        return
    
    name_lower = name.lower()
    
    if name_lower.endswith(".zip"):
        process_zip_data(data, patterns, counts, types, depth + 1)
    elif name_lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2")):
        files = extract_tar(data)
        for fname, fdata in files:
            process_file_content(fname, fdata, patterns, counts, types, depth + 1)


def process_zip_data(
    data_or_path: Any,
    patterns: dict[str, list],
    counts: dict[str, int],
    types: list[str],
    depth: int = 0,
) -> None:
    """处理 zip 文件内容"""
    try:
        if isinstance(data_or_path, bytes):
            zf = zipfile.ZipFile(io.BytesIO(data_or_path), "r")
        else:
            zf = zipfile.ZipFile(data_or_path, "r")
        
        with zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                try:
                    info = zf.getinfo(name)
                    if info.file_size > MAX_FILE_SIZE:
                        continue
                    content = zf.read(name)
                    process_file_content(name, content, patterns, counts, types, depth)
                except Exception:
                    continue
    except Exception:
        pass


def process_file_content(
    name: str,
    data: bytes,
    patterns: dict[str, list],
    counts: dict[str, int],
    types: list[str],
    depth: int = 0,
) -> None:
    """处理单个文件内容"""
    if depth > MAX_DEPTH or len(data) > MAX_FILE_SIZE:
        return
    name_lower = name.lower()
    
    # 检查是否为图片文件
    if any(name_lower.endswith(ext) for ext in IMAGE_EXTENSIONS):
        scan_image_bytes(name, data, patterns, counts, types)
        return
    
    # 检查是否为嵌套压缩包
    if name_lower.endswith(ARCHIVE_EXTENSIONS):
        extract_nested_archive(name, data, patterns, counts, types, depth)
        return
    
    # 尝试作为文本处理
    try:
        text = data.decode("utf-8", errors="ignore")
        file_counts = scan_text_content(text, patterns, types)
        for t, c in file_counts.items():
            counts[t] += c
    except Exception:
        pass


def scan_image_bytes(
    name: str,
    data: bytes,
    patterns: dict[str, list],
    counts: dict[str, int],
    types: list[str],
) -> None:
    """使用大模型识别图片中的敏感信息"""
    # 将图片转为 base64
    img_b64 = base64.b64encode(data).decode("utf-8")
    
    system_prompt = """你是一个敏感信息检测专家。

给定一张图片，请识别其中是否包含以下类型的敏感信息：
1. 手机号：以1开头的11位数字
2. 邮箱地址：符合user@domain.com格式
3. 身份证号：18位，最后一位可能为X
4. API Key：以sk-开头的字符串

请仔细查看图片中的所有文字，统计每种敏感信息的数量。

返回 JSON 格式：
{
  "phone": 0,
  "email": 0,
  "id_card": 0,
  "api_key": 0
}

如果没有发现任何敏感信息，返回全0。"""

    user_prompt = """请识别这张图片中的敏感信息数量。

返回 JSON 格式，包含 phone、email、id_card、api_key 四个字段的数量。"""

    async def _call_llm():
        load_dotenv()
        config = ModelConfig.from_env()
        client = ChatCompletionClient(config)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"
                        },
                    },
                ],
            },
        ]
        
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        return first_message(completion).get("content", "")

    try:
        content = asyncio.run(_call_llm())
        # 尝试解析 JSON
        try:
            img_counts = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", str(content), re.DOTALL)
            img_counts = json.loads(match.group(0)) if match else {}
        for t, c in img_counts.items():
            if t in counts:
                counts[t] += c
    except Exception:
        pass


def scan_zip_file(zip_path: Path, patterns: dict[str, list], types: list[str]) -> dict[str, int]:
    """扫描 zip 文件"""
    counts = init_counts(types)
    process_zip_data(zip_path, patterns, counts, types, 0)
    return counts


def scan_tar_file(tar_path: Path, patterns: dict[str, list], types: list[str]) -> dict[str, int]:
    counts = init_counts(types)
    try:
        for name, data in extract_tar(tar_path.read_bytes()):
            process_file_content(name, data, patterns, counts, types, 0)
    except Exception:
        pass
    return counts


def scan_path_recursive(path: Path, patterns: dict[str, list], types: list[str]) -> dict[str, int]:
    counts = init_counts(types)
    if path.is_dir():
        for file_path in path.rglob("*"):
            if file_path.is_file():
                file_counts = scan_path_recursive(file_path, patterns, types)
                for t, c in file_counts.items():
                    counts[t] += c
        return counts

    if not path.is_file() or path.stat().st_size > MAX_FILE_SIZE:
        return counts

    name_lower = path.name.lower()
    if name_lower.endswith(".zip"):
        return scan_zip_file(path, patterns, types)
    if name_lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")):
        return scan_tar_file(path, patterns, types)

    data = path.read_bytes()
    process_file_content(path.name, data, patterns, counts, types, 0)
    return counts


def build_patterns(payload: dict[str, Any]) -> tuple[dict[str, list], list[str]]:
    patterns = {key: list(value) for key, value in QUESTION_PATTERNS.items()}
    extra_patterns = payload.get("patterns")
    if isinstance(extra_patterns, dict):
        for key, value in extra_patterns.items():
            if isinstance(value, str):
                patterns[key] = [(value, key)]
            elif isinstance(value, list):
                patterns[key] = [(str(item), key) for item in value]
    types = payload.get("types")
    if not isinstance(types, list) or not types:
        types = DEFAULT_TYPES
    return patterns, [str(item) for item in types]


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    scan_path = payload.get("scan_path", "")
    log(f"scan_path: {scan_path}")
    
    if not scan_path:
        log("No scan_path provided")
        print(json.dumps({"error": "No scan_path provided"}, ensure_ascii=False))
        return

    path = Path(scan_path)
    if not path.exists():
        log(f"Path not found: {scan_path}")
        print(json.dumps({"error": f"Path not found: {scan_path}"}, ensure_ascii=False))
        return

    patterns, types = build_patterns(payload)
    counts = scan_path_recursive(path, patterns, types)

    log("Scan complete: " + ", ".join(f"{key}={counts.get(key, 0)}" for key in types))
    output = ",".join(str(counts.get(key, 0)) for key in types)
    print(json.dumps({"answer": output, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()