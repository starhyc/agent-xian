from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from source.solution.lib import skillio


TEXT_SUFFIXES = [".csv", ".md", ".txt", ".json", ".log", ".tsv"]
IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

PER_TEXT_CAP = 20000
TOTAL_TEXT_CAP = 120000
MAX_IMAGES = 40


def gather_text(per_cap: int = PER_TEXT_CAP, total_cap: int = TOTAL_TEXT_CAP) -> str:
    """Concatenate all declared text files, labelled by their relative path."""

    root = skillio.question_dir()
    blocks: List[str] = []
    total = 0
    for f in skillio.list_files():
        if f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            label = str(f.relative_to(root))
        except ValueError:
            label = f.name
        block = "\n\n===== 文件: %s =====\n%s" % (label, text[:per_cap])
        if total + len(block) > total_cap:
            block = block[: max(0, total_cap - total)]
        blocks.append(block)
        total += len(block)
        if total >= total_cap:
            break
    return "".join(blocks)


def ocr_images(prompt: str, max_images: int = MAX_IMAGES, max_tokens: int = 500) -> str:
    """OCR/extract content from all declared image files via the vision model."""

    from source.solution.lib.llm import ask_with_images

    root = skillio.question_dir()
    images = [f for f in skillio.list_files() if f.suffix.lower() in IMG_SUFFIXES]
    notes: List[str] = []
    for img in images[:max_images]:
        try:
            label = str(img.relative_to(root))
        except ValueError:
            label = img.name
        try:
            desc = ask_with_images(prompt, [img], max_tokens=max_tokens)
            notes.append("----- 图片: %s -----\n%s" % (label, desc.strip()))
        except Exception:
            continue
    return "\n\n".join(notes)
