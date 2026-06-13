from __future__ import annotations

import datetime
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


# Deterministic date solver covering the explanation's variant dimensions:
# 格式变种(点/斜杠/中文/混排/带时间戳/DD/MM/YYYY)、相对日期(今天/昨天/明天/去年今天)、
# 工作日推算、周次计算(上/下/这周X、财年第N周)、N小时/N天/N周后。
# It computes generically from rules (not hardcoded to any sample), so it
# generalizes to unseen dates/offsets. Whatever it cannot parse confidently
# falls back to a single batched LLM call — so common formats are instant and
# zero-token, while novel phrasings still get the model's best effort.

SYSTEM = (
    "你是严谨的日期解析器。逐行解析消息中描述的日期，统一转换为 yyyy-mm-dd。"
    "处理点/斜杠/中文/混排分隔符、带时间戳、相对日期(今天/明天/昨天/上周四/下周一/N个工作日后/周次/N小时后)等变种。"
    "相对日期以该行给出的基准日期为准；一周从周一开始；工作日跳过周六周日。"
    "每行恰好对应一个日期，输出顺序必须与输入行顺序一致。"
)

_CN = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}
_HOLIDAYS = {"儿童节": (6, 1), "劳动节": (5, 1), "国庆": (10, 1), "元旦": (1, 1), "圣诞": (12, 25), "妇女节": (3, 8)}


def _num(s):
    s = s.strip()
    return int(s) if s.isdigit() else _CN.get(s)


def _base_year(line):
    for pat in (r"今年是\s*(\d{4})", r"(\d{4})\s*年", r"(\d{4})"):
        m = re.search(pat, line)
        if m:
            return int(m.group(1))
    return 2026


def _tokens(line, year):
    """All explicit date tokens as ordered dates.

    Full dates (with year) sort before a bare month-day at the same position.
    """
    out = []
    for m in re.finditer(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", line):
        try:
            out.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{4})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})\s*日?", line):
        try:
            out.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", line):
        try:
            out.append((m.start() + 0.5, date(year, int(m.group(1)), int(m.group(2)))))
        except ValueError:
            pass
    out.sort(key=lambda t: t[0])
    return [d for _, d in out]


def _hour_of(line):
    if "零点" in line:
        return 0
    m = re.search(r"(\d{1,2})\s*点", line)
    return int(m.group(1)) if m else 0


def _add_workdays(d, n):
    step = 1 if n >= 0 else -1
    cnt = 0
    while cnt < abs(n):
        d += timedelta(days=step)
        if d.weekday() < 5:
            cnt += 1
    return d


def solve_line(line):
    """Return a date, or None if it can't be resolved deterministically."""
    year = _base_year(line)
    dates = _tokens(line, year)
    anchor = dates[0] if dates else None

    for key, (mo, dy) in _HOLIDAYS.items():
        if key in line:
            return date(year, mo, dy)

    # "下周X是<date>" establishes the reference week, then answer a 上周X query.
    m = re.search(r"下周([一二三四五六日天])是", line)
    if m and dates:
        nb = dates[0]
        next_monday = nb - timedelta(days=nb.weekday())
        this_monday = next_monday - timedelta(days=7)
        m2 = re.search(r"上周([一二三四五六日天])", line)
        if m2:
            return this_monday - timedelta(days=7) + timedelta(days=_WD[m2.group(1)])

    m = re.search(r"(\d+)\s*个?\s*小时", line)
    if m and anchor:
        dt = datetime.datetime(anchor.year, anchor.month, anchor.day, _hour_of(line)) + timedelta(hours=int(m.group(1)))
        return dt.date()

    m = re.search(r"(\d+)\s*个?\s*工作日", line)
    if m and anchor:
        return _add_workdays(anchor, int(m.group(1)))

    if "第" in line and "周" in line and dates:
        ms = re.search(r"第\s*(\d+)\s*周从", line)
        mt = re.search(r"第\s*(\d+)\s*周的周([一二三四五六日天])", line)
        if ms and mt:
            ws = dates[0]
            target_monday = ws - timedelta(days=ws.weekday()) + timedelta(days=7 * (int(mt.group(1)) - int(ms.group(1))))
            return target_monday + timedelta(days=_WD[mt.group(2)])

    m = re.search(r"(上|下|这|本)周\s*周?([一二三四五六日天])", line)
    if m and anchor:
        this_monday = anchor - timedelta(days=anchor.weekday())
        off = {"上": -7, "下": 7, "这": 0, "本": 0}[m.group(1)]
        return this_monday + timedelta(days=off) + timedelta(days=_WD[m.group(2)])

    if "去年" in line and anchor:
        try:
            return anchor.replace(year=anchor.year - 1)
        except ValueError:
            return anchor - timedelta(days=365)
    if "明天" in line and anchor:
        return anchor + timedelta(days=1)
    if "后天" in line and anchor:
        return anchor + timedelta(days=2)
    if "前天" in line and anchor:
        return anchor - timedelta(days=2)
    if "昨天" in line and anchor:
        return anchor - timedelta(days=1)

    m = re.search(r"(\d+|两|二|三)\s*周后", line)
    if m and anchor:
        return anchor + timedelta(days=7 * _num(m.group(1)))

    m = re.search(r"(\d+)\s*个?\s*(自然日|天)", line)
    if m and anchor:
        return anchor + timedelta(days=int(m.group(1)))

    # No arithmetic: the date the question refers to is the last-mentioned token.
    if dates:
        return dates[-1]
    return None


def _read_lines():
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


def _llm_fallback(lines, idxs, question):
    """One batched call for only the lines the parser could not resolve."""
    numbered = "\n".join("[%d] %s" % (i + 1, lines[i]) for i in idxs)
    prompt = (
        "题目要求：\n%s\n\n"
        "请逐行解析下面消息中的日期并转换为 yyyy-mm-dd，按给出的行号顺序、"
        "只输出英文逗号分隔的日期序列（数量与行数一致），不要行号或解释：\n%s"
        % (question, numbered)
    )
    out = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=800, enable_thinking=True)
    return re.findall(r"\d{4}-\d{2}-\d{2}", skillio.clean_answer(out))


def main() -> None:
    skillio.read_stdin_args()
    lines = _read_lines()
    question = skillio.question_text()

    results = [None] * len(lines)
    unresolved = []
    for i, ln in enumerate(lines):
        try:
            d = solve_line(ln)
        except Exception:
            d = None
        if d is not None:
            results[i] = d.strftime("%Y-%m-%d")
        else:
            unresolved.append(i)

    # Only the lines the deterministic solver could not handle hit the model,
    # batched into a single call to keep latency/token cost minimal.
    if unresolved:
        try:
            found = _llm_fallback(lines, unresolved, question)
        except Exception:
            found = []
        for k, i in enumerate(unresolved):
            results[i] = found[k] if k < len(found) else ""

    skillio.emit(",".join(r or "" for r in results))


if __name__ == "__main__":
    main()
