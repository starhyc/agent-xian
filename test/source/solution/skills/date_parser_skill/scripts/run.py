from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import json
import re
import sys
from pathlib import Path
from typing import Any

def log(msg: str) -> None:
    print(f"[date_parser] {msg}", file=sys.stderr)

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv

SYSTEM_PROMPT = """你是一个日期解析专家。

给定一批客服消息，每行一条消息，你需要从每条消息中解析出对应的日期，转换为 yyyy-mm-dd 格式。

基准日期是 {base_date}。

## 重要规则

1. **直接日期提取**：如果消息中明确给出了日期（如"合同签署日是2026年12月21日"、"发票日期是2026.03-15"），直接使用该日期。
   - 支持格式：2026.12.21、2026-03-15、2026年11月08日、2026年04月 17、15/06/2026 等

2. **相对日期计算**：
   - "上周X" = 基准日期所在周的上一周的星期X
     * 例如：基准日期 2026-05-06（周三），上周四 = 2026-05-01
   - "下周X" = 基准日期所在周的下一周的星期X
   - "昨天" = 基准日期 - 1天
   - "明天" = 基准日期 + 1天
   - "X天后" = 基准日期 + X天
   - "X小时后" = 基准日期 + X小时
   - "X个工作日后" = 只计算周一到周五，跳过周末
   - "第N周的周五" = 从基准日期开始计算第N周的周五
     * 例如：基准日期 2026-05-06，第2周的周五 = 2026-01-09
   - "倒计时X小时" = 基准日期 - X小时
   - "X天后" = 基准日期 + X天
   - "去年今天" = 基准日期的去年同一天

3. **日期来源优先级**：
   - 如果消息中明确给出了日期，使用该日期
   - 如果消息中需要计算相对日期，优先使用消息中明确提到的日期进行计算
   - 如果消息中没有明确日期，才使用基准日期

## 输出格式

返回JSON数组，每行对应一个日期字符串。
例如：["2026-12-21", "2026-03-15", "2026-05-06"]

不要输出其他内容，只输出JSON数组。"""


async def call_llm_parse_dates(text: str, base_date_str: str) -> list[str]:
    """调用大模型解析日期"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    system_prompt = SYSTEM_PROMPT.format(base_date=base_date_str)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"解析以下消息，每行一条，返回日期数组：\n{text}"}
    ]

    completion = await client.create(messages=messages, tools=[], tool_choice="none")
    content = str(first_message(completion).get("content") or "[]")

    # 解析 JSON 数组
    try:
        dates = json.loads(content)
        if isinstance(dates, list):
            return [str(d) for d in dates]
    except json.JSONDecodeError:
        pass

    return []


WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}

CHINESE_NUMBERS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def parse_int(text: str) -> int:
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[text]
    if text.startswith("十"):
        return 10 + CHINESE_NUMBERS.get(text[1:], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return CHINESE_NUMBERS.get(left, 1) * 10 + CHINESE_NUMBERS.get(right, 0)
    return 0


def to_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_string(value: str, default_year: int | None = None) -> date | None:
    patterns = [
        r"(?P<y>20\d{2})\s*[年./-]\s*(?P<m>\d{1,2})\s*[月./-]\s*(?P<d>\d{1,2})",
        r"(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>20\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return to_date(int(match.group("y")), int(match.group("m")), int(match.group("d")))

    if default_year:
        match = re.search(r"(?<!\d)(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?", value)
        if match:
            return to_date(default_year, int(match.group("m")), int(match.group("d")))
    return None


def extract_base_date(text: str, fallback: str) -> date:
    for pattern in [r"(?:今天|当前日期|基准日期)\D{0,6}(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2})", r"今年是\s*(20\d{2})\s*年"]:
        match = re.search(pattern, text)
        if match:
            if len(match.groups()) == 1 and match.group(1).isdigit():
                return date(int(match.group(1)), 1, 1)
            parsed = parse_date_string(match.group(1))
            if parsed:
                return parsed
    parsed_fallback = parse_date_string(fallback)
    return parsed_fallback or date(2026, 5, 6)


def add_workdays(start: date, days: int) -> date:
    current = start
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    while remaining:
        current += timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current


def week_relative(base: date, offset_weeks: int, weekday: int) -> date:
    monday = base - timedelta(days=base.weekday())
    return monday + timedelta(days=offset_weeks * 7 + weekday)


def select_explicit_date(line: str, base: date) -> date | None:
    explicit_dates: list[tuple[int, date]] = []
    for match in re.finditer(r"20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}|\d{1,2}/\d{1,2}/20\d{2}", line):
        parsed = parse_date_string(match.group(0), base.year)
        if parsed:
            explicit_dates.append((match.start(), parsed))
    default_year = explicit_dates[-1][1].year if explicit_dates else base.year
    for match in re.finditer(r"(?<![年\d])(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?", line):
        parsed = to_date(default_year, int(match.group("m")), int(match.group("d")))
        if parsed:
            explicit_dates.append((match.start(), parsed))
    if explicit_dates:
        return sorted(explicit_dates, key=lambda item: item[0])[-1][1]
    return parse_date_string(line, base.year)


def datetime_from_line(line: str, day: date) -> datetime:
    if "零点" in line:
        return datetime.combine(day, datetime.min.time())
    match = re.search(r"(\d{1,2})\s*(?:点|时)", line)
    hour = int(match.group(1)) if match else 0
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)


def parse_line_with_rules(line: str, global_base: date) -> str | None:
    line_base = extract_base_date(line, global_base.isoformat())
    explicit = select_explicit_date(line, line_base)
    default_year = explicit.year if explicit else line_base.year

    if "儿童节" in line:
        year_match = re.search(r"(20\d{2})年", line)
        year = int(year_match.group(1)) if year_match else default_year
        return date(year, 6, 1).isoformat()

    if "去年今天" in line:
        return date(line_base.year - 1, line_base.month, line_base.day).isoformat()
    if "昨天" in line:
        return (line_base - timedelta(days=1)).isoformat()
    if "明天" in line:
        return (line_base + timedelta(days=1)).isoformat()

    week_matches = list(re.finditer(r"(上|下|本|这)周(?:周|星期|礼拜)?([一二三四五六日天1-7])", line))
    if week_matches:
        match = week_matches[-1]
        offset = {"上": -1, "下": 1, "本": 0, "这": 0}[match.group(1)]
        return week_relative(line_base, offset, WEEKDAY_MAP[match.group(2)]).isoformat()

    match = re.search(r"第([一二两三四五六七八九十\d]+)周(?:的)?(?:周|星期|礼拜)?([一二三四五六日天1-7])", line)
    if match:
        week_no = parse_int(match.group(1))
        weekday = WEEKDAY_MAP[match.group(2)]
        start = explicit or line_base
        monday = start - timedelta(days=start.weekday())
        if re.search(r"第[一二两三四五六七八九十\d]+周从", line):
            return (monday + timedelta(days=weekday)).isoformat()
        return (monday + timedelta(weeks=max(week_no - 1, 0), days=weekday)).isoformat()

    match = re.search(r"([一二两三四五六七八九十\d]+)个?工作日(后|前)", line)
    if match:
        days = parse_int(match.group(1))
        if match.group(2) == "前":
            days = -days
        return add_workdays(explicit or line_base, days).isoformat()

    match = re.search(r"(?:倒计时|往?后推|推)?([一二两三四五六七八九十\d]+)个?小时(之后|以后|后|前)?", line)
    if match:
        hours = parse_int(match.group(1))
        delta = timedelta(hours=hours)
        base_dt = datetime_from_line(line, explicit or line_base)
        result = base_dt - delta if match.group(2) == "前" else base_dt + delta
        return result.date().isoformat()

    match = re.search(r"([一二两三四五六七八九十\d]+)周(后|前|之后|以后)", line)
    if match:
        weeks = parse_int(match.group(1))
        if match.group(2) == "前":
            weeks = -weeks
        return ((explicit or line_base) + timedelta(weeks=weeks)).isoformat()

    match = re.search(r"([一二两三四五六七八九十\d]+)(?:个)?(?:自然日|天|日)(后|前|之后|以后)", line)
    if match:
        days = parse_int(match.group(1))
        if match.group(2) == "前":
            days = -days
        return ((explicit or line_base) + timedelta(days=days)).isoformat()

    match = re.search(r"([一二两三四五六七八九十\d]+)(?:个)?(?:自然日|天|日)(?:送达|无理由|自提|退货)", line)
    if match:
        return ((explicit or line_base) + timedelta(days=parse_int(match.group(1)))).isoformat()

    match = re.search(r"试用期([一二两三四五六七八九十\d]+)天", line)
    if match and explicit:
        return (explicit + timedelta(days=parse_int(match.group(1)))).isoformat()

    if explicit:
        return explicit.isoformat()

    month_day = parse_date_string(line, default_year)
    if month_day:
        return month_day.isoformat()
    return None


async def parse_dates(text: str, base_date_str: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    global_base = extract_base_date(text, base_date_str)
    results: list[str | None] = [parse_line_with_rules(line, global_base) for line in lines]

    missing = [idx for idx, value in enumerate(results) if not value]
    if missing:
        fallback_text = "\n".join(lines[idx] for idx in missing)
        try:
            fallback_dates = await call_llm_parse_dates(fallback_text, global_base.isoformat())
        except Exception as exc:
            log(f"LLM fallback failed: {exc}")
            fallback_dates = []
        for idx, value in zip(missing, fallback_dates):
            parsed = parse_date_string(value)
            results[idx] = parsed.isoformat() if parsed else value

    if any(not value for value in results):
        try:
            all_dates = await call_llm_parse_dates(text, global_base.isoformat())
        except Exception as exc:
            log(f"Full LLM fallback failed: {exc}")
            all_dates = []
        if len(all_dates) == len(lines):
            results = all_dates

    return [str(value or global_base.isoformat()) for value in results]


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    text = payload.get("text", "")
    base_date_str = payload.get("base_date", "2026-05-06")
    
    log(f"Input text lines: {len(text.splitlines()) if text else 0}")
    log(f"Base date: {base_date_str}")

    if not text:
        log("No text provided, returning empty")
        print(json.dumps({
            "error": "No text provided",
            "dates": [],
        }, ensure_ascii=False))
        return

    log("Parsing dates with rules and LLM fallback...")
    dates = asyncio.run(parse_dates(text, base_date_str))
    log(f"Parser returned {len(dates)} dates")

    print(json.dumps({
        "dates": dates,
        "count": len(dates),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()