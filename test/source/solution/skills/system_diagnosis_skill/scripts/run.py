from __future__ import annotations

import asyncio
import json
import re
import sys
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log(msg: str) -> None:
    print(f"[system_diagnosis] {msg}", file=sys.stderr)


def extract_zip(zip_path: str, extract_to: str) -> list[str]:
    """安全解压 zip 文件"""
    extracted_files = []
    try:
        output_dir = Path(extract_to).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                member_path = (output_dir / member.filename).resolve()
                if not member_path.is_relative_to(output_dir):
                    log(f"Skip unsafe zip member: {member.filename}")
                    continue
                if member.is_dir():
                    member_path.mkdir(parents=True, exist_ok=True)
                    continue
                member_path.parent.mkdir(parents=True, exist_ok=True)
                member_path.write_bytes(zf.read(member))
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


ID_FIELDS = [
    "workflowId", "actionId", "requestId", "traceId", "validationRef",
    "operationId", "taskId", "pageId",
]
META_FIELDS = [
    "module", "pageGroup", "displayObject", "interface", "api", "path",
    "url", "method", "status", "stage", "source", "validationCode",
    "fieldPath", "schemaVersion", "screenshotFile", "result", "message",
]
STATIC_SUFFIXES = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".map",
    ".woff", ".woff2", ".ttf",
)
NOISE_WORDS = ("diagnostic", "诊断", "replay", "回放", "precheck", "预校验", "static", "后台", "background")


def parse_json_object(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            full_key = f"{prefix}.{key}" if prefix else str(key)
            result[full_key] = item
            result.update(flatten_json(item, full_key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(flatten_json(item, f"{prefix}.{index}" if prefix else str(index)))
    return result


def simple_key(flat_key: str) -> str:
    return flat_key.rsplit(".", 1)[-1]


def extract_fields_from_text(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    data = parse_json_object(text)
    if data is not None:
        for key, value in flatten_json(data).items():
            skey = simple_key(key)
            if skey in ID_FIELDS or skey in META_FIELDS:
                fields[skey] = value

    for key in ID_FIELDS + META_FIELDS:
        patterns = [
            rf'"{re.escape(key)}"\s*:\s*"([^"]+)"',
            rf"\b{re.escape(key)}\b\s*[:=]\s*([^\s,;|]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match and key not in fields:
                fields[key] = match.group(1).strip("\"'")
    return fields


def normalize_record(fields: dict[str, Any], raw: str, source: str) -> dict[str, Any]:
    normalized = {key: str(value) for key, value in fields.items() if value is not None and str(value) != ""}
    normalized["_raw"] = raw[:2000]
    normalized["_source"] = source
    return normalized


def parse_log_records(content: str, source: str) -> list[dict[str, Any]]:
    records = []
    for line in content.splitlines():
        clean = line.strip()
        if not clean:
            continue
        fields = extract_fields_from_text(clean)
        if fields:
            records.append(normalize_record(fields, clean, source))
    return records


def parse_body_fields(text: str) -> dict[str, Any]:
    if not text:
        return {}
    data = parse_json_object(text)
    if data is None:
        return extract_fields_from_text(text)
    fields: dict[str, Any] = {}
    for key, value in flatten_json(data).items():
        skey = simple_key(key)
        if skey in ID_FIELDS or skey in META_FIELDS:
            fields[skey] = value
    return fields


def parse_har_records(content: str) -> list[dict[str, Any]]:
    try:
        har = json.loads(content)
    except Exception:
        return parse_log_records(content, "har")
    entries = har.get("log", {}).get("entries", []) if isinstance(har, dict) else []
    records = []
    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        url = request.get("url", "")
        parsed = urllib.parse.urlparse(url)
        fields: dict[str, Any] = {
            "url": url,
            "path": parsed.path,
            "method": request.get("method", ""),
            "status": response.get("status", ""),
        }
        for item in request.get("headers", []) + request.get("queryString", []):
            name = item.get("name")
            if name in ID_FIELDS + META_FIELDS:
                fields[name] = item.get("value", "")
        fields.update(parse_body_fields(request.get("postData", {}).get("text", "")))
        fields.update(parse_body_fields(response.get("content", {}).get("text", "")))
        records.append(normalize_record(fields, json.dumps(entry, ensure_ascii=False)[:3000], "har"))
    return records


def record_ids(record: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (key, record[key])
        for key in ID_FIELDS
        if record.get(key)
    }


def is_noise(record: dict[str, Any]) -> bool:
    raw = (record.get("_raw", "") + " " + " ".join(str(record.get(key, "")) for key in META_FIELDS)).lower()
    path = str(record.get("path") or urllib.parse.urlparse(str(record.get("url", ""))).path).lower()
    if path.endswith(STATIC_SUFFIXES):
        return True
    return any(word.lower() in raw for word in NOISE_WORDS)


def is_business_request(record: dict[str, Any]) -> bool:
    if record.get("_source") != "har" or is_noise(record):
        return False
    path = str(record.get("path") or urllib.parse.urlparse(str(record.get("url", ""))).path)
    return bool(path and path != "/")


def records_share_id(left: dict[str, Any], right: dict[str, Any], known_ids: set[tuple[str, str]]) -> bool:
    left_ids = record_ids(left)
    right_ids = record_ids(right)
    return bool((left_ids & right_ids) or (right_ids & known_ids) or (left_ids & known_ids))


def build_candidate_flows(frontend: list[dict[str, Any]], backend: list[dict[str, Any]], har: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seeds = [record for record in har if is_business_request(record)] or backend
    flows = []
    for seed in seeds:
        known_ids = set(record_ids(seed))
        front_matches: list[dict[str, Any]] = []
        back_matches: list[dict[str, Any]] = []

        changed = True
        while changed:
            changed = False
            for record in frontend:
                if record not in front_matches and records_share_id(seed, record, known_ids):
                    front_matches.append(record)
                    new_ids = record_ids(record) - known_ids
                    known_ids.update(new_ids)
                    changed = changed or bool(new_ids)
            for record in backend:
                if record not in back_matches and records_share_id(seed, record, known_ids):
                    back_matches.append(record)
                    new_ids = record_ids(record) - known_ids
                    known_ids.update(new_ids)
                    changed = changed or bool(new_ids)

        flow_records = [seed] + front_matches + back_matches
        score = 0
        score += 4 if seed.get("_source") == "har" else 0
        score += 3 if front_matches else 0
        score += 4 if any(record.get("validationCode") for record in back_matches) else 0
        score += 2 if any(str(record.get("status", "")).startswith(("4", "5")) for record in flow_records) else 0
        score -= 5 if any(is_noise(record) for record in flow_records) else 0
        flows.append({"seed": seed, "frontend": front_matches, "backend": back_matches, "records": flow_records, "score": score})
    return sorted(flows, key=lambda item: item["score"], reverse=True)


def load_schema(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def rule_conditions(rule: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        "rootCause", "rootCauseKeyword", "cause", "description", "name", "id",
        "message", "reason",
    }
    return {key: value for key, value in rule.items() if key not in ignored}


def field_matches(actual: Any, expected: Any) -> bool:
    if expected in (None, "", [], {}):
        return True
    if isinstance(expected, list):
        return any(field_matches(actual, item) for item in expected)
    actual_text = str(actual or "")
    expected_text = str(expected)
    return actual_text == expected_text or expected_text in actual_text


def record_matches(record: dict[str, Any], conditions: dict[str, Any]) -> bool:
    for key, expected in conditions.items():
        if key in {"all", "any"}:
            continue
        if not field_matches(record.get(key), expected):
            return False
    all_rules = conditions.get("all")
    if isinstance(all_rules, list):
        return all(record_matches(record, item) for item in all_rules if isinstance(item, dict))
    any_rules = conditions.get("any")
    if isinstance(any_rules, list):
        return any(record_matches(record, item) for item in any_rules if isinstance(item, dict))
    return True


def apply_effective_rule(records: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
    rule = schema.get("effectiveValidationRule")
    if not isinstance(rule, dict):
        return records
    conditions = rule_conditions(rule)
    return [record for record in records if record_matches(record, conditions)]


def unique_in_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def resolve_root_causes(records: list[dict[str, Any]], schema: dict[str, Any]) -> list[str]:
    records = apply_effective_rule(records, schema)
    causes: list[str] = []

    rules = schema.get("rootCauseRules")
    if isinstance(rules, list):
        for record in records:
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if record_matches(record, rule_conditions(rule)):
                    cause = rule.get("rootCause") or rule.get("rootCauseKeyword") or rule.get("cause")
                    if cause:
                        causes.append(str(cause))

    code_map = schema.get("validationCodeMap")
    if isinstance(code_map, dict):
        for record in records:
            code = record.get("validationCode")
            if code and code in code_map:
                causes.append(str(code_map[code]))

    return unique_in_order(causes)


def resolve_module(flow: dict[str, Any], schema: dict[str, Any]) -> str:
    for record in flow.get("records", []):
        for key in ("module", "pageGroup", "displayObject"):
            if record.get(key):
                return str(record[key]).replace("模块", "")
    for rule in schema.get("rootCauseRules", []) if isinstance(schema.get("rootCauseRules"), list) else []:
        if isinstance(rule, dict) and rule.get("module"):
            return str(rule["module"]).replace("模块", "")
    return "未知"


def resolve_api(flow: dict[str, Any]) -> str:
    seed = flow.get("seed", {})
    for record in [seed] + flow.get("records", []):
        path = record.get("path") or record.get("api") or record.get("interface")
        if path:
            return urllib.parse.urlparse(str(path)).path or str(path)
        url = record.get("url")
        if url:
            return urllib.parse.urlparse(str(url)).path
    return ""


def analyze_structured(frontend_log: str, backend_log: str, har_content: str, form_schema: str) -> str | None:
    frontend_records = [record for record in parse_log_records(frontend_log, "frontend") if not is_noise(record)]
    backend_records = [record for record in parse_log_records(backend_log, "backend") if not is_noise(record)]
    har_records = parse_har_records(har_content)
    schema = load_schema(form_schema)

    flows = build_candidate_flows(frontend_records, backend_records, har_records)
    for flow in flows:
        validation_records = [record for record in flow.get("backend", []) if record.get("validationCode")]
        if not validation_records:
            continue
        causes = resolve_root_causes(validation_records, schema)
        if causes:
            module = resolve_module(flow, schema)
            api = resolve_api(flow)
            if api:
                return f"{module},{api},{'、'.join(causes)}"
    return None


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

    log("Analyzing structured evidence...")
    answer = analyze_structured(frontend_log, backend_log, har_content, form_schema)
    if not answer:
        log("Structured analysis inconclusive, calling LLM fallback...")
        result = call_llm_analyze(frontend_log, backend_log, har_content, form_schema)
        answer = result.get("answer", "")

    log(f"Answer: {answer}")
    print(json.dumps({"answer": answer}, ensure_ascii=False))


if __name__ == "__main__":
    main()