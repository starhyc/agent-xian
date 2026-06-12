from __future__ import annotations

import asyncio
import json
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

    # 调用大模型解析
    log("Calling LLM to parse dates...")
    dates = asyncio.run(call_llm_parse_dates(text, base_date_str))
    log(f"LLM returned {len(dates)} dates")

    print(json.dumps({
        "dates": dates,
        "count": len(dates),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()