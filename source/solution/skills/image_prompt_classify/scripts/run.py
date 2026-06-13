from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask_with_images


IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def _index(path: Path) -> int:
    m = re.search(r"\d+", path.stem)
    return int(m.group(0)) if m else 0


def _split_train_val():
    roots = skillio.allowed_paths()
    train_dir = None
    val_dir = None
    for root in roots:
        base = root if root.is_dir() else root.parent
        has_label = any(p.suffix.lower() == ".txt" for p in base.rglob("*"))
        name = base.name
        if has_label or "训练" in name or "train" in name.lower():
            train_dir = base
        elif "验证" in name or "val" in name.lower() or "test" in name.lower():
            val_dir = base
    # Fallback by label presence if names didn't disambiguate.
    if train_dir is None or val_dir is None:
        labelled, unlabelled = [], []
        for root in roots:
            base = root if root.is_dir() else root.parent
            if any(p.suffix.lower() == ".txt" for p in base.rglob("*")):
                labelled.append(base)
            else:
                unlabelled.append(base)
        train_dir = train_dir or (labelled[0] if labelled else None)
        val_dir = val_dir or (unlabelled[0] if unlabelled else None)
    return train_dir, val_dir


def _load_train(train_dir: Path):
    samples = []
    if not train_dir:
        return samples
    for img in sorted(train_dir.rglob("*")):
        if img.suffix.lower() not in IMG_SUFFIXES:
            continue
        label_file = img.with_suffix(".txt")
        if not label_file.exists():
            continue
        try:
            label = label_file.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if label:
            samples.append((img, label.splitlines()[0].strip()))
    return samples


def _learn_rules(samples, question):
    by_label = {}
    for img, label in samples:
        by_label.setdefault(label, []).append(img)
    labels = sorted(by_label)
    chosen = []
    chosen_labels = []
    for label in labels:
        for img in by_label[label][:4]:
            chosen.append(img)
            chosen_labels.append(label)
        if len(chosen) >= 16:
            break
    desc = "\n".join("第%d张图的标签是 %s" % (i + 1, lb) for i, lb in enumerate(chosen_labels))
    prompt = (
        "任务说明：\n%s\n\n"
        "下面按顺序给出训练样本图片，其标签为：\n%s\n\n"
        "请对照这些图片与标签，总结一套清晰、可判别、覆盖全部类别边界的判别规则，"
        "用于对新图片分类。类别集合固定为：%s。只输出判别规则文字，简洁但充分。"
        % (question, desc, ", ".join(labels))
    )
    try:
        rules = ask_with_images(prompt, chosen, max_tokens=1200)
    except Exception:
        rules = ""
    return labels, skillio.clean_answer(rules)


def _classify(img, labels, rules, question):
    label_set = ", ".join(labels)
    prompt = (
        "任务说明：\n%s\n\n判别规则：\n%s\n\n"
        "请判断这张图片的类别，只能从 [%s] 中选择一个，"
        "只输出该类别本身，不要任何解释或多余字符。"
        % (question, rules, label_set)
    )
    try:
        out = ask_with_images(prompt, [img], max_tokens=30)
    except Exception:
        out = ""
    out = skillio.clean_answer(out).upper()
    # Prefer exact label match; else substring; else first label.
    upper_labels = [(lb, lb.upper()) for lb in labels]
    for lb, up in upper_labels:
        if out == up:
            return lb
    for lb, up in upper_labels:
        if up in out:
            return lb
    return labels[0] if labels else out


def main() -> None:
    skillio.read_stdin_args()
    question = skillio.question_text()
    train_dir, val_dir = _split_train_val()
    samples = _load_train(train_dir)
    labels, rules = _learn_rules(samples, question)
    if not labels:
        labels = sorted({lb for _, lb in samples}) or ["PASS", "FAIL", "NOT_INVOLVED"]

    val_imgs = []
    if val_dir:
        val_imgs = sorted(
            (p for p in val_dir.rglob("*") if p.suffix.lower() in IMG_SUFFIXES),
            key=_index,
        )
    parts = []
    for img in val_imgs:
        pred = _classify(img, labels, rules, question)
        parts.append("%d%s" % (_index(img), pred))
    skillio.emit(",".join(parts))


if __name__ == "__main__":
    main()
