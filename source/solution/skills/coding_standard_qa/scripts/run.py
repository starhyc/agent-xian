from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


SYSTEM = (
    "你是编程规范答疑助手。只依据提供的规范文档原文作答。"
    "即使问题以英文提问，也要按规范文档中的原文措辞回答，不要翻译、不要改写。"
    "答案要短、精确，只给关键词或原文取值，不要解释。"
)

PER_FILE_CAP = 24000
TOTAL_CAP = 90000


def _load_docs() -> str:
    files = skillio.list_files([".md", ".txt", ".rst", ".pdf", ".doc", ".docx"]) or skillio.list_files()
    chunks = []
    total = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        text = text[:PER_FILE_CAP]
        block = "\n\n===== 文档: %s =====\n%s" % (f.name, text)
        if total + len(block) > TOTAL_CAP:
            block = block[: max(0, TOTAL_CAP - total)]
        chunks.append(block)
        total += len(block)
        if total >= TOTAL_CAP:
            break
    return "".join(chunks)


def main() -> None:
    skillio.read_stdin_args()
    docs = _load_docs()
    question = skillio.question_text()
    prompt = (
        "以下是规范文档全文：\n%s\n\n"
        "题目（包含需要回答的问题列表与返回格式）：\n%s\n\n"
        "请严格依据上面文档原文，按题面问题顺序逐题作答，"
        "答案之间用英文分号 ; 分隔，数量与问题数一致；"
        "只输出答案串，不要输出 Q1/Q2 等标签或任何解释。"
        % (docs, question)
    )
    answer = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=1500, enable_thinking=True)
    skillio.emit(answer)


if __name__ == "__main__":
    main()
