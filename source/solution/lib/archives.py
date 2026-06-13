from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import List


"""Recursive archive extraction (zip + tar, nested) into a target directory.

Used by the sensitive-info scan and defect-localization skills. Nested archives
are expanded in place so downstream code can walk a flat directory tree.
"""

_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2"}


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError:
            continue  # skip path traversal entries
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    safe_members = []
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError:
            continue
        safe_members.append(member)
    tf.extractall(dest, members=safe_members)


def extract_all(archive_path: Path, dest: Path, _depth: int = 0) -> Path:
    """Extract an archive into ``dest`` and recursively expand nested archives."""

    dest.mkdir(parents=True, exist_ok=True)
    path = Path(archive_path)
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                _safe_extract_zip(zf, dest)
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as tf:
                _safe_extract_tar(tf, dest)
    except Exception:
        return dest

    if _depth < 8:
        for inner in list(dest.rglob("*")):
            if inner.is_file() and inner.suffix.lower() in _ARCHIVE_SUFFIXES:
                sub = inner.parent / (inner.name + "__extracted")
                if sub.exists():
                    continue
                if zipfile.is_zipfile(inner) or tarfile.is_tarfile(inner):
                    extract_all(inner, sub, _depth + 1)
    return dest


def walk_files(root: Path) -> List[Path]:
    return sorted(p for p in Path(root).rglob("*") if p.is_file())
