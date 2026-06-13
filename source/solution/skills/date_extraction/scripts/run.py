from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


SYSTEM = (
    "你是严谨的日期解析器。逐行解析消息中描述的日期，统一转换为 yyyy-mm-dd。"
    "处理点/斜杠/中文/混排分隔符、带时间戳、相对日期(今天/明天/上周四/下周一/N个工作日后/周次)等变种。"
    "相对日期以该行或题面给出的基准日期为准；一周从周一开始；工作日跳过周六周日。"
    "每行恰好对应一个日期，输出顺序必须与输入行顺序一致。"
)


def _read_lines() -> list:
    files = skillio.list_files([".txt", ".log", ".csv", ".md"]) or skillio.list_files()
    lines = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for raw in text.splitlines():
            s = raw.strip()
            if s:
                lines.append(s)
    return lines


def main() -> None:
    skillio.read_stdin_args()
    lines = _read_lines()
    question = skillio.question_text()
    numbered = "\n".join("[%d] %s" % (i + 1, ln) for i, ln in enumerate(lines))

    prompt = (
        "题目要求：\n%s\n\n"
        "以下是按行编号的消息（每行解析出一个日期）：\n%s\n\n"
        "请逐行解析日期并转换为 yyyy-mm-dd，按行号顺序输出，"
        "只输出英文逗号分隔的日期序列，不要输出行号、空格或任何解释。"
        % (question, numbered)
    )
    answer = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=2048, enable_thinking=True)
    skillio.emit(answer)


if __name__ == "__main__":
    main()
