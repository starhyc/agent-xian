from __future__ import annotations

import asyncio
import json
import re
import sys
import time
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


def get_nested_value(data: dict[str, Any], path: str) -> Any:
    """Get nested value from dict using dot notation (e.g., 'data.userId')."""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def validate_assertion(response_body: dict[str, Any], assertion: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate response against assertion criteria."""
    errors = []

    expected_status = assertion.get("expectedStatus", 200)
    actual_status = response_body.get("_status_code", 200)
    if actual_status != expected_status:
        errors.append(f"status: expected {expected_status}, got {actual_status}")

    for field in assertion.get("expectedFields", []):
        value = get_nested_value(response_body, field)
        if value is None:
            errors.append(f"missing field: {field}")

    for field, expected_value in assertion.get("expectedValues", {}).items():
        actual_value = get_nested_value(response_body, field)
        if actual_value != expected_value:
            errors.append(f"{field}: expected {expected_value!r}, got {actual_value!r}")

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
        return json.loads(test_cases_content)
    except json.JSONDecodeError:
        return []


def parse_auth_config(auth_config_content: str) -> dict[str, Any]:
    """解析鉴权配置"""
    try:
        return json.loads(auth_config_content)
    except json.JSONDecodeError:
        return {}


def build_request_from_test_case(
    test_case: dict[str, Any],
    apis: dict[str, dict[str, Any]],
) -> tuple[str, str, dict[str, Any] | None]:
    """根据测试用例构建请求"""
    description = test_case.get("description", "")
    request = test_case.get("request", {})

    # 获取接口路径
    api_path = request.get("api", "")
    if not api_path:
        # 尝试从 description 中提取
        for path in apis.keys():
            if path in description.lower():
                api_path = path
                break

    if not api_path or api_path.lower() not in apis:
        return "", "", None

    api_def = apis[api_path.lower()]
    method = request.get("method", api_def.get("method", "GET"))
    params = request.get("params", {})
    body = request.get("body", {})

    # 构建完整 URL
    full_path = api_path
    if params:
        query_parts = [f"{k}={v}" for k, v in params.items()]
        full_path += "?" + "&".join(query_parts)

    return full_path, method, body


def execute_test_case(
    base_url: str,
    package_id: str,
    test_case: dict[str, Any],
    current_token: dict[str, str] | None,
    auth_config: dict[str, Any],
) -> tuple[bool, dict[str, Any], dict[str, str] | None]:
    """执行单个测试用例"""
    description = test_case.get("description", "")
    assertion = test_case.get("assert", {})

    headers = {
        "X-Package-Id": package_id,
    }

    # 判断是否需要鉴权
    requires_auth = test_case.get("requiresAuth", True)
    if requires_auth and current_token:
        headers["Authorization"] = f"Bearer {current_token.get('accessToken', '')}"

    # 判断是否需要获取新 token
    needs_new_token = test_case.get("getToken", False)
    if needs_new_token:
        token_url = f"{base_url}/api/auth/token"
        token_response = make_request(
            token_url,
            method="POST",
            headers={**headers, "Content-Type": "application/json"},
            body={
                "clientId": auth_config.get("clientId", "agent_demo_client"),
                "clientSecret": auth_config.get("clientSecret", "agent_demo_secret"),
            },
        )
        if token_response.get("success"):
            token_data = token_response.get("body", {})
            access_token = token_data.get("data", {}).get("accessToken", "")
            if access_token:
                current_token = {"accessToken": access_token}
                headers["Authorization"] = f"Bearer {access_token}"

    # 获取请求信息
    api_path = test_case.get("api", "")
    method = test_case.get("method", "GET")
    body = test_case.get("body")

    # 执行请求
    url = f"{base_url}{api_path}"
    response = make_request(url, method, headers, body)

    if not response.get("success"):
        return False, {"error": response.get("error", "Request failed")}, current_token

    response_body = response.get("body", {})
    response_body["_status_code"] = response.get("status_code", 200)

    return True, response_body, current_token


async def run_test_cases(
    base_url: str,
    package_id: str,
    test_cases: list[dict[str, Any]],
    auth_config: dict[str, Any],
) -> list[str]:
    """运行所有测试用例，返回失败的用例 ID"""
    # 先调用 reset 接口
    reset_url = f"{base_url}/api/debug/reset"
    make_request(
        reset_url,
        method="POST",
        headers={"X-Package-Id": package_id},
    )
    time.sleep(0.5)

    failed_cases = []
    current_token = None

    for test_case in test_cases:
        passed, response_body, current_token = execute_test_case(
            base_url, package_id, test_case, current_token, auth_config
        )

        # 验证断言
        assertion = test_case.get("assert", {})
        if assertion:
            passed, errors = await call_llm_validate(response_body, assertion)
        else:
            passed = response_body.get("_status_code", 200) < 400

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
    package_id = payload.get("package_id", "")
    docs_dir = payload.get("docs_directory", ".")

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
    failed_cases = asyncio.run(run_test_cases(base_url, package_id, test_cases, auth_config))
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