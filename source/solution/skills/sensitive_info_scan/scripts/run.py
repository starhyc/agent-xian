from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.archives import extract_all, walk_files
from source.solution.lib.llm import ask_with_images


# Default sensitive-info patterns (overridable conceptually by the question).
RE_PHONE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.com\b")
RE_IDCARD = re.compile(r"(?<![\dXx])\d{17}[\dXx](?![\dXx])")
RE_APIKEY = re.compile(r"sk-[A-Za-z0-9]+")

IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
TEXT_SUFFIXES = {".txt", ".log", ".csv", ".json", ".md", ".tsv", ".xml", ".html", ".ini", ".cfg", ""}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2"}

OCR_PROMPT = "请逐字转写图片中所有可见文本（包括手机号、邮箱、身份证号、API Key、sk- 开头字符串等），原样输出，不要解释。"


def _count(text, counters):
    counters["phone"] += len(RE_PHONE.findall(text))
    counters["email"] += len(RE_EMAIL.findall(text))
    counters["idcard"] += len(RE_IDCARD.findall(text))
    counters["apikey"] += len(RE_APIKEY.findall(text))


def _read_text(path):
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def main() -> None:
    skillio.read_stdin_args()
    counters = {"phone": 0, "email": 0, "idcard": 0, "apikey": 0}

    archives = [f for f in skillio.list_files() if f.suffix.lower() in ARCHIVE_SUFFIXES]
    workdir = Path(tempfile.mkdtemp(prefix="sens_scan_"))
    roots = []
    for arc in archives:
        dest = workdir / (arc.stem + "__x")
        try:
            extract_all(arc, dest)
            roots.append(dest)
        except Exception:
            pass
    # Also scan any non-archive declared files directly.
    direct = [f for f in skillio.list_files() if f.suffix.lower() not in ARCHIVE_SUFFIXES]

    files = []
    for r in roots:
        files.extend(walk_files(r))
    files.extend(direct)

    for f in files:
        suf = f.suffix.lower()
        if suf in IMG_SUFFIXES:
            try:
                text = ask_with_images(OCR_PROMPT, [f], max_tokens=1200)
            except Exception:
                text = ""
            _count(text, counters)
        elif suf in TEXT_SUFFIXES or suf not in ARCHIVE_SUFFIXES:
            _count(_read_text(f), counters)

    skillio.emit("%d,%d,%d,%d" % (counters["phone"], counters["email"], counters["idcard"], counters["apikey"]))


if __name__ == "__main__":
    main()
