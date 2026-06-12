from __future__ import annotations

import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log(msg: str) -> None:
    print(f"[system_diagnosis] {msg}", file=sys.stderr)


def extract_zip(zip_path: str, extract_to: str) -> list[str]:
    """解压 zip 文件"""
    extracted_files = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_to)
            extracted_files = zf.namelist()
    except Exception:
        pass
    return extracted_files


def read_file_safe(file_path: str) -> str:
    """安全读取文件"""
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def call_llm_analyze(
    frontend_log: str,
    backend_log: str,
    har_content: str,
    form_schema: str,
) -> dict:
    """使用大模型分析问题并确定根因"""
    system_prompt = """你是一个系统问题诊断专家。

给定前端日志、后端校验日志、网络抓包记录、截图列表和校验规则，你需要：
1. 识别所有可能的候选作业流（actionId、requestId、traceId、validationRef 关联）
2. 筛选出"完整前台作业流"：页面记录 → 前端事件 → 网络请求 → 后端校验 → 最终可见失败
3. 排除干扰项：诊断回放、预校验、后台任务、相邻重试、静态资源异常
4. 根据 form_schema.json 的映射规则，确定根因关键词

form_schema.json 可能包含：
- validationCodeMap：validationCode 到根因关键词的映射
- effectiveValidationRule：有效校验码的过滤条件
- rootCauseRules：根因规则匹配条件

输出格式（只输出这一行，不要其他内容）：
缺陷模块,异常接口,根因关键词1、根因关键词2、...

注意：
- 缺陷模块不要加"模块"后缀
- 多个根因关键词用中文顿号连接
- 只输出能形成完整前台作业流的证据链对应的结果"""

    user_prompt = f"""【前端日志】（可能包含 actionId、requestId、traceId、workflowId、pageTime、pageGroup、displayObject、screenshotFile、submit result 等）：
{frontend_log[:6000]}

【后端校验日志】（可能包含 validationRef、requestId、traceId、stage、source、validationCode、fieldPath、schemaVersion 等）：
{backend_log[:6000]}

【HAR 文件】（网络抓包，可能包含接口路径、状态码、actionId、requestId、traceId、validationRef 等）：
{har_content[:4000]}

【校验规则】（form_schema.json，可能包含 validationCodeMap、effectiveValidationRule、rootCauseRules 等）：
{form_schema[:3000]}

请分析并只输出答案行，格式如：用户管理,/api/user/update,接口契约未同步、字段映射关系错误"""

    async def _call_llm():
        load_dotenv()
        config = ModelConfig.from_env()
        client = ChatCompletionClient(config)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        return first_message(completion).get("content", "")

    try:
        content = asyncio.run(_call_llm())
        log("LLM analysis completed")
        return {"answer": content}
    except Exception as exc:
        log(f"LLM call failed: {exc}")
        return {"answer": f"API error: {exc}"}


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    base_dir = payload.get("base_dir", "")
    log(f"base_dir: {base_dir}")
    
    if not base_dir:
        log("No base_dir provided")
        print(json.dumps({"error": "No base_dir provided"}, ensure_ascii=False))
        return

    base_path = Path(base_dir)

    # 解压截图包（可选，可能包含干扰项）
    screenshot_zip = base_path / "defect_screenshots.zip"
    screenshot_dir = base_path / "screenshots"
    if screenshot_zip.exists():
        log("Extracting screenshots zip...")
        extract_zip(str(screenshot_zip), str(screenshot_dir))

    # 读取文件
    log("Reading log files...")
    frontend_log = read_file_safe(str(base_path / "frontend_log.log"))
    backend_log = read_file_safe(str(base_path / "backend_validation.log"))
    har_content = read_file_safe(str(base_path / "network.har"))
    form_schema = read_file_safe(str(base_path / "form_schema.json"))
    
    log(f"Loaded: frontend_log={len(frontend_log)} chars, backend_log={len(backend_log)} chars, har={len(har_content)} chars")

    # 调用大模型分析
    log("Calling LLM for analysis...")
    result = call_llm_analyze(frontend_log, backend_log, har_content, form_schema)

    # 输出答案
    answer = result.get("answer", "")
    log(f"Answer: {answer}")
    print(json.dumps({"answer": answer}, ensure_ascii=False))


if __name__ == "__main__":
    main()