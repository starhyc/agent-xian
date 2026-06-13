from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


PLAN_SYSTEM = (
    "你是接口测试执行规划器。给定接口文档、鉴权配置和单个用例描述，"
    "把用例需要的真实调用规划成有序请求列表，并指出最终要校验哪一次响应。"
    "只输出 JSON，不要解释。"
)


def _load():
    api_doc = ""
    test_cases = []
    auth = {}
    for f in skillio.list_files():
        name = f.name.lower()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if name.endswith("api_doc.md") or (name.endswith(".md") and not api_doc):
            api_doc = text
        elif "test_case" in name or name.endswith("cases.json"):
            try:
                test_cases = json.loads(text)
            except Exception:
                pass
        elif "auth" in name and name.endswith(".json"):
            try:
                auth = json.loads(text)
            except Exception:
                pass
    return api_doc, test_cases, auth


def _base_url(auth, api_doc):
    if isinstance(auth, dict) and auth.get("baseUrl"):
        return str(auth["baseUrl"]).rstrip("/")
    m = re.search(r"https?://127\.0\.0\.1:\d+", api_doc or "")
    return m.group(0) if m else "http://127.0.0.1:18081"


def _pkg_header(auth):
    if isinstance(auth, dict) and auth.get("packageIdHeader"):
        return str(auth["packageIdHeader"])
    return "X-Package-Id"


def _dotted(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None, False
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, False
    return cur, True


def _http(method, url, headers, body):
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception:
        return -1, None, ""
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    return status, parsed, raw


def _plan(api_doc, auth, case):
    prompt = (
        "接口文档：\n%s\n\n鉴权配置：\n%s\n\n"
        "用例：\n%s\n\n"
        "请输出 JSON：{\"requests\":[{\"method\":..,\"path\":\"/api/..\",\"headers\":{..},\"body\":{..}|null,"
        "\"is_token\":true|false}], \"assert_index\": <要校验响应的请求下标,从0开始>}。\n"
        "规则：path 用相对路径(以/开头)。需要写接口或用例说要获取token时，先放一个取 token 请求并设 is_token=true；"
        "写接口的 headers 里 Authorization 用字符串 \"Bearer ${accessToken}\"；"
        "若用例要求复用旧 token 则不要再加取 token 请求，直接用 \"Bearer ${accessToken}\"；"
        "assert_index 指向用例描述中明确要校验的那一次响应(通常是最后一次查询/目标接口)。只输出 JSON。"
        % (api_doc[:6000], json.dumps(auth, ensure_ascii=False), json.dumps(case, ensure_ascii=False))
    )
    text = ask(prompt, system=PLAN_SYSTEM, temperature=0.0, max_tokens=1200, enable_thinking=False)
    text = skillio.clean_answer(text)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _token_path(auth):
    if isinstance(auth, dict):
        return auth.get("token", {}).get("responseTokenPath", "data.accessToken")
    return "data.accessToken"


def _run_case(base, pkg_header, pkg_value, auth, case, state):
    plan = _plan(_run_case._api_doc, auth, case)
    if not plan or not isinstance(plan.get("requests"), list) or not plan["requests"]:
        return False
    responses = []
    tok_path = _token_path(auth)
    for reqspec in plan["requests"]:
        method = str(reqspec.get("method", "GET"))
        path = str(reqspec.get("path", ""))
        url = base + path if path.startswith("/") else path
        headers = {pkg_header: pkg_value}
        for k, v in (reqspec.get("headers") or {}).items():
            if isinstance(v, str) and "${accessToken}" in v:
                v = v.replace("${accessToken}", state.get("token", ""))
            headers[k] = v
        status, parsed, _raw = _http(method, url, headers, reqspec.get("body"))
        responses.append((status, parsed))
        if reqspec.get("is_token") and parsed is not None:
            val, ok = _dotted(parsed, tok_path)
            if ok and val:
                state["token"] = str(val)
    idx = plan.get("assert_index", len(responses) - 1)
    if not isinstance(idx, int) or idx < 0 or idx >= len(responses):
        idx = len(responses) - 1
    status, parsed = responses[idx]
    return _check(status, parsed, case.get("assert", {}))


def _check(status, parsed, asrt):
    if "expectedStatus" in asrt and status != asrt["expectedStatus"]:
        return False
    for field in asrt.get("expectedFields", []):
        _v, ok = _dotted(parsed if parsed is not None else {}, field)
        if not ok:
            return False
    for path, expected in (asrt.get("expectedValues") or {}).items():
        actual, ok = _dotted(parsed if parsed is not None else {}, path)
        if not ok or not _eq(actual, expected):
            return False
    return True


def _eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return str(a) == str(b)


def main() -> None:
    skillio.read_stdin_args()
    api_doc, cases, auth = _load()
    _run_case._api_doc = api_doc
    base = _base_url(auth, api_doc)
    pkg_header = _pkg_header(auth)
    pkg_value = os.environ.get("PACKAGE_ID", "") or os.environ.get("packageId", "")

    # Reset run-state for this packageId so results are reproducible.
    try:
        _http("POST", base + "/api/debug/reset", {pkg_header: pkg_value}, None)
    except Exception:
        pass

    state = {"token": ""}
    failed = []
    for case in cases:
        cid = case.get("id", "")
        try:
            ok = _run_case(base, pkg_header, pkg_value, auth, case, state)
        except Exception:
            ok = False
        if not ok:
            failed.append(cid)
    skillio.emit(",".join(failed))


if __name__ == "__main__":
    main()
