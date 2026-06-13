from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.archives import extract_all, walk_files
from source.solution.lib.llm import ask, ask_with_images


SYSTEM = (
    "你是系统问题定位专家。你需要从多份材料中抽取证据，"
    "找出唯一一条能跨文件闭环的完整前台作业流（页面→前端提交→网络请求→后端校验→可见失败），"
    "并严格按 form_schema 规则把有效 validationCode 映射为根因关键词。"
    "排除相似截图、重试、诊断回放、预校验、静态资源异常、后台任务等干扰，不要跨 requestId/actionId/traceId/validationRef 合并。"
)

TEXT_SUFFIXES = [".log", ".har", ".json", ".txt", ".md", ".csv"]
IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz"}
MAX_IMAGES = 12
PER_FILE_CAP = 30000


def _collect_text_and_images():
    text_blocks = []
    images = []
    files = skillio.list_files()
    for f in files:
        suf = f.suffix.lower()
        if suf in ARCHIVE_SUFFIXES:
            dest = f.parent / (f.stem + "__x")
            try:
                extract_all(f, dest)
                for inner in walk_files(dest):
                    if inner.suffix.lower() in IMG_SUFFIXES:
                        images.append(inner)
                    elif inner.suffix.lower() in TEXT_SUFFIXES:
                        text_blocks.append((inner.name, _read(inner)))
            except Exception:
                pass
        elif suf in IMG_SUFFIXES:
            images.append(f)
        elif suf in TEXT_SUFFIXES:
            text_blocks.append((f.name, _read(f)))
    return text_blocks, images


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:PER_FILE_CAP]
    except Exception:
        return ""


def _ocr_images(images):
    notes = []
    for img in images[:MAX_IMAGES]:
        try:
            desc = ask_with_images(
                "这是问题定位用的页面截图。请简洁描述：页面标题/页面组、操作对象、可见的报错或失败信息、"
                "页面时间、以及任何定位标识(actionId/requestId/traceId/截图文件名标识)。只输出要点。",
                [img],
                max_tokens=400,
            )
            notes.append("截图 %s: %s" % (img.name, desc.strip()))
        except Exception:
            continue
    return notes


def main() -> None:
    skillio.read_stdin_args()
    text_blocks, images = _collect_text_and_images()
    question = skillio.question_text()

    evidence = []
    for name, content in text_blocks:
        evidence.append("===== 文件: %s =====\n%s" % (name, content))
    image_notes = _ocr_images(images) if images else []
    if image_notes:
        evidence.append("===== 截图视觉抽取 =====\n" + "\n".join(image_notes))

    prompt = (
        "题目与判定要求：\n%s\n\n"
        "证据材料如下：\n%s\n\n"
        "请先在心里构建候选作业流，选出唯一能跨文件闭环的完整前台作业流，"
        "再按 form_schema 规则把该链路下的有效 validationCode 映射为根因关键词。"
        "最终只输出一行：缺陷模块,异常接口,根因关键词。多个根因关键词用中文顿号 、 连接，不要任何解释。"
        % (question, "\n\n".join(evidence))
    )
    answer = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=800, enable_thinking=True)
    skillio.emit(answer)


if __name__ == "__main__":
    main()
