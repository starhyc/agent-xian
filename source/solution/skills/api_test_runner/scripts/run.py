from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


PLAN_SYSTEM = (
    "你是接口测试执行规划器。给定接口文档、鉴权配置和单个用例描述，"
    "把用例需要的真实调用规划成有序的 HTTP 请求列表，并指出最终要校验哪一次响应。"
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


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _build_url(base, path, query):
    """Join base + path and merge any structured query params into the URL.

    Query params explicitly belong in the URL query string, never in the body.
    A path that already carries `?a=b` is preserved and merged with `query`.
    """
    if path.startswith("/"):
        url = base + path
    else:
        url = path
    if not query:
        return url
    parts = urllib.parse.urlsplit(url)
    merged = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    for k, v in query.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        merged.append((str(k), str(v)))
    new_query = urllib.parse.urlencode(merged)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _http(method, url, headers, body, send_body):
    data = None
    if send_body and body is not None:
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


def _token_cfg(auth):
    tok = auth.get("token", {}) if isinstance(auth, dict) else {}
    return {
        "endpoint": tok.get("endpoint", "/api/auth/token"),
        "method": str(tok.get("method", "POST")),
        "headers": tok.get("headers", {"Content-Type": "application/json"}),
        "body": tok.get("body", {}),
        "path": tok.get("responseTokenPath", "data.accessToken"),
        "fmt": tok.get("authorizationHeaderFormat", "Bearer ${accessToken}"),
    }


def _auth_header_name(auth):
    if isinstance(auth, dict):
        name = auth.get("authorizationHeader") or auth.get("token", {}).get("authorizationHeaderName")
        if name:
            return str(name)
    return "Authorization"


def _fetch_token(base, pkg_header, pkg_value, auth):
    cfg = _token_cfg(auth)
    headers = {pkg_header: pkg_value}
    for k, v in (cfg["headers"] or {}).items():
        headers[k] = v
    url = _build_url(base, cfg["endpoint"], None)
    status, parsed, _raw = _http(cfg["method"], url, headers, cfg["body"], send_body=True)
    if parsed is not None:
        val, ok = _dotted(parsed, cfg["path"])
        if ok and val:
            return str(val)
    return ""


def _plan(api_doc, auth, case):
    prompt = (
        "接口文档：\n%s\n\n鉴权配置：\n%s\n\n"
        "用例：\n%s\n\n"
        "请输出 JSON：{\"requests\":[{\"method\":\"GET|POST|PUT|DELETE\",\"path\":\"/api/..\","
        "\"query\":{..}|null,\"body\":{..}|null,\"is_write\":true|false,\"reuse_token\":true|false}],"
        "\"assert_index\": <要校验响应的请求下标,从0开始>}。\n"
        "规则：\n"
        "1) path 用相对路径(以/开头)，路径参数(如 userId)直接写进 path，例如 /api/user/detail/U1010。\n"
        "2) 所有查询参数(分页 page/pageSize、过滤 status/department/keyword、排序 sortOrder、verbose 等)"
        "必须放进 query 对象，绝对不要拼进 path、也不要放进 body。读接口的 body 必须为 null。\n"
        "3) 写接口(新增/更新/删除/批量/备注等，通常是 POST/PUT/DELETE)把 is_write 设为 true，"
        "请求体放进 body；不要自己加取 token 的请求，运行器会自动取 token 并注入 Authorization。\n"
        "4) 仅当用例明确要求“复用上一个/旧 token 并校验 401”时，把该写请求的 reuse_token 设为 true。\n"
        "5) 不要规划 /api/auth/token、/api/debug/reset 请求，运行器自动处理。\n"
        "6) assert_index 指向用例描述中明确要校验的那一次响应(通常是最后一次查询/目标接口)。只输出 JSON。"
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


def _is_write(reqspec):
    if isinstance(reqspec.get("is_write"), bool):
        return reqspec["is_write"]
    return str(reqspec.get("method", "GET")).upper() in _WRITE_METHODS


def execute_plan(base, pkg_header, pkg_value, auth, plan, case, state):
    """Execute a planned request list and assert the targeted response.

    Token lifecycle is owned here, not by the planner: every write fetches a
    fresh token (honouring "one token = one successful write"), unless the
    request is explicitly flagged reuse_token (for cases that verify a 401).
    """
    if not plan or not isinstance(plan.get("requests"), list) or not plan["requests"]:
        return False
    auth_name = _auth_header_name(auth)
    fmt = _token_cfg(auth)["fmt"]
    responses = []
    for reqspec in plan["requests"]:
        method = str(reqspec.get("method", "GET"))
        path = str(reqspec.get("path", ""))
        url = _build_url(base, path, reqspec.get("query"))
        headers = {pkg_header: pkg_value}
        is_write = _is_write(reqspec)
        if is_write:
            if reqspec.get("reuse_token"):
                token = state.get("token", "")
            else:
                token = _fetch_token(base, pkg_header, pkg_value, auth)
                if token:
                    state["token"] = token
            if token:
                headers[auth_name] = fmt.replace("${accessToken}", token)
        # Any explicit auth-style headers from the planner are merged last,
        # with ${accessToken} resolved against the current token.
        for k, v in (reqspec.get("headers") or {}).items():
            if isinstance(v, str) and "${accessToken}" in v:
                v = v.replace("${accessToken}", state.get("token", ""))
            headers[k] = v
        status, parsed, _raw = _http(method, url, headers, reqspec.get("body"), send_body=is_write)
        responses.append((status, parsed))
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
    base = _base_url(auth, api_doc)
    pkg_header = _pkg_header(auth)
    pkg_value = os.environ.get("PACKAGE_ID", "") or os.environ.get("packageId", "")

    # Reset run-state for this packageId so results are reproducible.
    try:
        _http("POST", _build_url(base, "/api/debug/reset", None), {pkg_header: pkg_value}, None, send_body=False)
    except Exception:
        pass

    state = {"token": ""}
    failed = []
    for case in cases:
        cid = case.get("id", "")
        try:
            plan = _plan(api_doc, auth, case)
            ok = execute_plan(base, pkg_header, pkg_value, auth, plan, case, state)
        except Exception:
            ok = False
        if not ok:
            failed.append(cid)
    skillio.emit(",".join(failed))


if __name__ == "__main__":
    main()
