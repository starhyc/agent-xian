from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log(msg: str) -> None:
    print(f"[api_tester] {msg}", file=sys.stderr)


def read_file_safe(file_path: str) -> str:
    """安全读取文件"""
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def make_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make an HTTP request and return detailed results."""
    start_time = time.time()

    request_kwargs: dict[str, Any] = {
        "url": url,
        "method": method.upper(),
        "timeout": timeout,
    }
    if headers:
        request_kwargs["headers"] = headers
    if params:
        request_kwargs["params"] = params
    if body and method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        request_kwargs["json"] = body

    try:
        response = requests.request(**request_kwargs)
        elapsed_ms = int((time.time() - start_time) * 1000)

        try:
            response_body = response.json()
        except json.JSONDecodeError:
            response_body = {"_raw_text": response.text}

        return {
            "success": True,
            "status_code": response.status_code,
            "body": response_body,
            "elapsed_ms": elapsed_ms,
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Request timeout",
        }
    except requests.exceptions.ConnectionError as exc:
        return {
            "success": False,
            "error": f"Connection failed: {exc}",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }


def get_nested_value(data: Any, path: str) -> Any:
    """Get nested value using dot notation, including list indexes."""
    if path in ("", "$"):
        return data
    keys = path[2:].split(".") if path.startswith("$.") else path.split(".")
    current: Any = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and key.isdigit():
            index = int(key)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def validate_assertion(response_body: dict[str, Any], assertion: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate response against assertion criteria."""
    errors = []

    expected_status = assertion.get("expectedStatus", assertion.get("status", assertion.get("statusCode")))
    actual_status = response_body.get("_status_code", 200)
    if expected_status is not None and actual_status != expected_status:
        errors.append(f"status: expected {expected_status}, got {actual_status}")

    for field in assertion.get("expectedFields", []):
        value = get_nested_value(response_body, field)
        if value is None:
            errors.append(f"missing field: {field}")

    for field, expected_value in assertion.get("expectedValues", {}).items():
        actual_value = get_nested_value(response_body, field)
        if actual_value != expected_value:
            errors.append(f"{field}: expected {expected_value!r}, got {actual_value!r}")

    for item in assertion.get("contains", []):
        if isinstance(item, dict):
            field = str(item.get("field", ""))
            expected = item.get("value")
            actual = get_nested_value(response_body, field)
        else:
            field = "$"
            expected = item
            actual = response_body
        if str(expected) not in json.dumps(actual, ensure_ascii=False):
            errors.append(f"{field}: expected to contain {expected!r}")

    for item in assertion.get("notContains", []):
        if isinstance(item, dict):
            field = str(item.get("field", ""))
            expected = item.get("value")
            actual = get_nested_value(response_body, field)
        else:
            field = "$"
            expected = item
            actual = response_body
        if str(expected) in json.dumps(actual, ensure_ascii=False):
            errors.append(f"{field}: expected not to contain {expected!r}")

    type_map = {"string": str, "str": str, "number": (int, float), "int": int, "integer": int, "bool": bool, "boolean": bool, "array": list, "list": list, "object": dict, "dict": dict}
    expected_types = assertion.get("types", assertion.get("expectedTypes", {}))
    for field, expected_type in expected_types.items():
        actual = get_nested_value(response_body, field)
        py_type = type_map.get(str(expected_type).lower())
        if py_type and not isinstance(actual, py_type):
            errors.append(f"{field}: expected type {expected_type}, got {type(actual).__name__}")

    return len(errors) == 0, errors


async def call_llm_validate(response_body: dict[str, Any], assertion: dict[str, Any]) -> tuple[bool, list[str]]:
    """使用大模型验证断言"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    system_prompt = """你是一个 API 测试断言验证专家。

给定 API 响应体和断言期望，你需要判断断言是否通过。

规则：
1. 严格匹配：expectedStatus 必须完全相等
2. 字段存在：expectedFields 中的每个字段都必须存在
3. 值匹配：expectedValues 中的每个键值对都必须完全相等（类型和值）
4. 注意：响应体中的 _status_code 是实际的 HTTP 状态码

返回 JSON 格式：
{"passed": true/false, "errors": ["error1", "error2", ...]}
如果通过，errors 为空数组。"""

    user_prompt = f"""API 响应体：
{json.dumps(response_body, ensure_ascii=False, indent=2)}

断言期望：
{json.dumps(assertion, ensure_ascii=False, indent=2)}

判断断言是否通过，返回 JSON。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        content = str(first_message(completion).get("content") or "{}")
        result = json.loads(content)
        passed = result.get("passed", False)
        errors = result.get("errors", [])
        return passed, errors
    except Exception:
        return validate_assertion(response_body, assertion)


def parse_api_doc(api_doc_content: str) -> dict[str, dict[str, Any]]:
    """解析 api_doc.md，提取接口定义"""
    apis = {}

    # 提取接口定义（支持 Markdown 格式）
    # 格式: ### POST /api/user/update
    pattern = r"###\s+(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)"
    matches = re.findall(pattern, api_doc_content)

    for method, path in matches:
        apis[path.lower()] = {
            "method": method.upper(),
            "path": path,
        }

    return apis


def parse_test_cases(test_cases_content: str) -> list[dict[str, Any]]:
    """解析测试用例"""
    try:
        data = json.loads(test_cases_content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("test_cases", "cases", "tests"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []
    except json.JSONDecodeError:
        return []


def parse_auth_config(auth_config_content: str) -> dict[str, Any]:
    """解析鉴权配置"""
    try:
        return json.loads(auth_config_content)
    except json.JSONDecodeError:
        return {}


def interpolate(value: Any, variables: dict[str, Any]) -> Any:
    """替换 ${var} 变量引用。"""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            replacement = variables.get(match.group(1), "")
            return str(replacement)
        return re.sub(r"\$\{([^}]+)\}", repl, value)
    if isinstance(value, list):
        return [interpolate(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: interpolate(item, variables) for key, item in value.items()}
    return value


def join_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def infer_api_definition(api_path: str, apis: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return apis.get(api_path.lower(), {})


def auth_spec(auth_config: dict[str, Any]) -> dict[str, Any]:
    auth = auth_config.get("auth") if isinstance(auth_config.get("auth"), dict) else auth_config
    return {
        "path": auth.get("path") or auth.get("authPath") or auth.get("tokenPath") or auth.get("url") or "/api/auth/token",
        "method": auth.get("method", "POST"),
        "headers": auth.get("headers", {}),
        "body": auth.get("body") or auth.get("json") or {
            key: auth.get(key)
            for key in ("clientId", "clientSecret", "username", "password")
            if auth.get(key) is not None
        },
        "token_path": auth.get("tokenResponsePath") or auth.get("accessTokenPath") or auth.get("tokenPathInResponse") or "data.accessToken",
        "header_name": auth.get("authorizationHeader") or auth.get("headerName") or "Authorization",
        "header_template": auth.get("authorizationTemplate") or auth.get("headerTemplate") or "Bearer ${token}",
    }


def ensure_token(base_url: str, package_id: str, auth_config: dict[str, Any], variables: dict[str, Any]) -> str:
    if variables.get("token"):
        return str(variables["token"])
    spec = auth_spec(auth_config)
    response = make_request(
        join_url(base_url, str(spec["path"])),
        method=str(spec["method"]),
        headers={**spec.get("headers", {}), "X-Package-Id": package_id},
        body=spec.get("body") or {},
    )
    body = response.get("body", {}) if response.get("success") else {}
    token = get_nested_value(body, str(spec["token_path"]))
    if token is None:
        for path in ("accessToken", "token", "data.token", "data.access_token", "access_token"):
            token = get_nested_value(body, path)
            if token:
                break
    if token:
        variables["token"] = token
    return str(token or "")


def build_headers(
    package_id: str,
    step: dict[str, Any],
    auth_config: dict[str, Any],
    variables: dict[str, Any],
    base_url: str,
) -> dict[str, str]:
    headers = {"X-Package-Id": package_id}
    headers.update(auth_config.get("headers", {}) if isinstance(auth_config.get("headers"), dict) else {})
    headers.update(step.get("headers", {}) if isinstance(step.get("headers"), dict) else {})
    requires_auth = step.get("requiresAuth", step.get("auth", True))
    if requires_auth:
        token = ensure_token(base_url, package_id, auth_config, variables)
        if token:
            spec = auth_spec(auth_config)
            headers[str(spec["header_name"])] = interpolate(str(spec["header_template"]), {"token": token})
    return {key: str(value) for key, value in headers.items()}


def request_from_step(step: dict[str, Any], apis: dict[str, dict[str, Any]]) -> dict[str, Any]:
    request = step.get("request") if isinstance(step.get("request"), dict) else step
    api_path = request.get("api") or request.get("path") or request.get("url") or request.get("endpoint") or ""
    api_def = infer_api_definition(str(api_path), apis)
    method = request.get("method") or api_def.get("method") or "GET"
    return {
        "path": api_path or api_def.get("path", ""),
        "method": method,
        "params": request.get("params") or request.get("query") or {},
        "body": request.get("body") if "body" in request else request.get("json"),
        "headers": request.get("headers") or {},
    }


def response_with_status(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body", {})
    if not isinstance(body, dict):
        body = {"_value": body}
    body["_status_code"] = response.get("status_code", 0)
    return body


def extract_variables(extract_spec: dict[str, str], response_body: dict[str, Any], variables: dict[str, Any]) -> None:
    for name, path in extract_spec.items():
        value = get_nested_value(response_body, path)
        if value is not None:
            variables[name] = value


def collect_assertions(step: dict[str, Any], case_assertion: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    assertions = []
    for key in ("assert", "assertion", "expect", "expected"):
        value = step.get(key)
        if isinstance(value, dict):
            assertions.append(value)
        elif isinstance(value, list):
            assertions.extend(item for item in value if isinstance(item, dict))
    if not assertions and case_assertion:
        assertions.append(case_assertion)
    return assertions


def execute_step(
    base_url: str,
    package_id: str,
    step: dict[str, Any],
    apis: dict[str, dict[str, Any]],
    auth_config: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    request = interpolate(request_from_step(step, apis), variables)
    request_step = {**step, "headers": request.get("headers", {})}
    headers = build_headers(package_id, request_step, auth_config, variables, base_url)
    response = make_request(
        join_url(base_url, str(request["path"])),
        method=str(request["method"]),
        headers=headers,
        body=request.get("body"),
        params=request.get("params"),
    )
    response_body = response_with_status(response) if response.get("success") else {"_status_code": 0, "error": response.get("error", "Request failed")}
    extract_spec = step.get("extract") or step.get("extractVariables") or step.get("save") or {}
    if isinstance(extract_spec, dict):
        extract_variables(extract_spec, response_body, variables)
    return response_body


async def execute_test_case(
    base_url: str,
    package_id: str,
    test_case: dict[str, Any],
    apis: dict[str, dict[str, Any]],
    auth_config: dict[str, Any],
    variables: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """执行单个或多步骤用例。"""
    case_assertion = test_case.get("assert") if isinstance(test_case.get("assert"), dict) else None
    steps = test_case.get("steps") if isinstance(test_case.get("steps"), list) else [test_case]
    last_response: dict[str, Any] = {}
    errors: list[str] = []

    if test_case.get("getToken"):
        ensure_token(base_url, package_id, auth_config, variables)

    for step in steps:
        last_response = execute_step(base_url, package_id, step, apis, auth_config, variables)
        assertions = collect_assertions(step, case_assertion if step is steps[-1] else None)
        if assertions:
            for assertion in assertions:
                passed, assertion_errors = validate_assertion(last_response, assertion)
                if not passed:
                    errors.extend(assertion_errors)
        elif last_response.get("_status_code", 0) >= 400 or last_response.get("_status_code", 0) == 0:
            errors.append(f"status: got {last_response.get('_status_code')}")

    if errors:
        last_response["_errors"] = errors
    return not errors, last_response


async def run_test_cases(
    base_url: str,
    package_id: str,
    test_cases: list[dict[str, Any]],
    auth_config: dict[str, Any],
    apis: dict[str, dict[str, Any]],
    reset_first: bool = False,
) -> list[str]:
    """运行所有测试用例，返回失败的用例 ID"""
    if reset_first:
        reset_path = auth_config.get("resetPath", "/api/debug/reset")
        make_request(join_url(base_url, reset_path), method="POST", headers={"X-Package-Id": package_id})
        time.sleep(0.5)

    failed_cases = []
    variables: dict[str, Any] = {}

    for test_case in test_cases:
        passed, _response_body = await execute_test_case(base_url, package_id, test_case, apis, auth_config, variables)
        if not passed:
            failed_cases.append(test_case.get("id", ""))

    return failed_cases


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    base_url = payload.get("base_url", "http://127.0.0.1:18081")
    package_id = payload.get("package_id") or "api-tester-default"
    docs_dir = payload.get("docs_directory", ".")
    reset_first = bool(payload.get("reset_first", False))

    log(f"base_url: {base_url}, package_id: {package_id}")
    log(f"docs_dir: {docs_dir}")
    
    base_path = Path(docs_dir)

    # 读取配置文件
    log("Reading config files...")
    api_doc_content = read_file_safe(str(base_path / "api_doc.md"))
    test_cases_content = read_file_safe(str(base_path / "test_cases.json"))
    auth_config_content = read_file_safe(str(base_path / "auth_config.json"))

    # 解析配置
    log("Parsing config...")
    apis = parse_api_doc(api_doc_content)
    test_cases = parse_test_cases(test_cases_content)
    auth_config = parse_auth_config(auth_config_content)
    
    log(f"Found {len(apis)} APIs, {len(test_cases)} test cases")

    if not test_cases:
        log("No test cases found, returning empty")
        print(json.dumps({
            "error": "No test cases found",
            "failed_cases": [],
        }, ensure_ascii=False))
        return

    # 运行测试用例
    log("Running test cases...")
    failed_cases = asyncio.run(run_test_cases(base_url, package_id, test_cases, auth_config, apis, reset_first))
    log(f"Test completed: {len(test_cases) - len(failed_cases)} passed, {len(failed_cases)} failed")

    print(json.dumps({
        "total": len(test_cases),
        "passed": len(test_cases) - len(failed_cases),
        "failed": len(failed_cases),
        "failed_cases": failed_cases,
        "answer": ",".join(failed_cases),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()