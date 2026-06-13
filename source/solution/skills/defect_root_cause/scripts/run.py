from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


# Root-cause localization is structured correlation, not vision: build the one
# complete foreground workflow by linking ids across frontend log / HAR /
# backend validation, then map its validationCodes through form_schema. The
# screenshot package is an explicit distractor (per the task), so we never OCR
# it. Only if the deterministic chain can't be resolved do we fall back to a
# single text-only LLM call.

SYSTEM = (
    "你是系统问题定位专家。从前端日志、网络抓包、后端校验和 form_schema 中，"
    "找出唯一能跨文件闭环的完整前台作业流（页面→前端提交→网络请求→后端校验→可见失败），"
    "按 form_schema 规则把该链路下的有效 validationCode 映射为根因关键词。"
    "排除相似截图、重试、诊断回放、预校验、静态资源、后台任务等干扰，不跨 workflowId/requestId/actionId/validationRef 合并。"
    "只输出一行：缺陷模块,异常接口,根因关键词（多个根因用中文顿号、连接），不要解释。"
)

EXCLUDE_ROLES = {"background-job", "other-user-operation", "diagnostic-replay"}
_KV = re.compile(r'(\w+)=("[^"]*"|[^,\s]+)')


def _kv(line):
    return {k: v.strip('"') for k, v in _KV.findall(line)}


def _classify(files):
    har = schema = frontend = backend = None
    others = []
    for f in files:
        suf = f.suffix.lower()
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            head = ""
        if suf == ".har":
            har = f
        elif suf == ".json" and re.search(r"validationCodeMap|rootCauseRules|effectiveValidationRule", head):
            schema = f
        elif suf == ".log" and "validationRef" in head:
            backend = f
        elif suf == ".log" and re.search(r"workflowId|actionId|submitSource|workflow step", head):
            frontend = f
        else:
            others.append((f, head))
    return har, schema, frontend, backend, others


def _parse_backend(text):
    by_ref = {}
    ref_meta = {}
    for ln in text.splitlines():
        d = _kv(ln)
        ref = d.get("validationRef")
        if not ref:
            continue
        ref_meta.setdefault(ref, {}).update({k: d[k] for k in ("stage", "source", "workflowRole", "replayGroup") if k in d})
        if "validationCode" in d:
            by_ref.setdefault(ref, []).append((d["validationCode"], d))
    return by_ref, ref_meta


def _parse_har(text):
    try:
        h = json.loads(text)
        raw_entries = h["log"]["entries"]
    except Exception:
        return []
    entries = []
    for e in raw_entries:
        req = e.get("request", {})
        res = e.get("response", {})
        hdr = {x.get("name", "").lower(): x.get("value", "") for x in req.get("headers", [])}
        rb = (res.get("content", {}) or {}).get("text", "")
        try:
            rj = json.loads(rb) if rb else {}
        except Exception:
            rj = {}
        if not isinstance(rj, dict):
            rj = {}
        url = req.get("url", "")
        entries.append({
            "method": req.get("method", ""),
            "path": re.sub(r"^https?://[^/]+", "", url).split("?")[0],
            "status": res.get("status"),
            "actionId": hdr.get("x-action-id") or hdr.get("actionid") or rj.get("actionId"),
            "resp": rj,
        })
    return entries


def _foreground_action(fe_events, har):
    # Prefer the frontend terminal visible failure that is a foreground action.
    for d in fe_events:
        if d.get("finalUiError") == "true" and d.get("workflowRole", "") not in EXCLUDE_ROLES and not d.get("retryOf"):
            if d.get("actionId"):
                return d["actionId"]
    for d in fe_events:
        if d.get("submitSource") == "foreground-user-action" and d.get("actionId"):
            return d["actionId"]
    # Else from HAR: a request whose response marks foreground + has validationRef.
    for e in har:
        r = e["resp"]
        if r.get("validationRef") and r.get("workflowRole") == "foreground-user-action":
            return e["actionId"]
    return None


def _apply_schema(schema, codes, code_fields, module):
    vmap = schema.get("validationCodeMap", {})
    if isinstance(schema.get("rootCauseRules"), list):
        out = []
        for rule in schema["rootCauseRules"]:
            vc = rule.get("validationCode")
            mod_ok = (not rule.get("module")) or rule.get("module") == module
            if vc in codes and mod_ok:
                rc = rule.get("rootCause")
                if rc and rc not in out:
                    out.append(rc)
        return out
    eff = schema.get("effectiveValidationRule")
    out = []
    for code in codes:
        if isinstance(eff, dict):
            fields = code_fields.get(code, {})
            if not all(str(fields.get(k)) == str(v) for k, v in eff.items()):
                continue
        if code in vmap:
            out.append(vmap[code])
    return out


def _solve(har, schema_f, frontend, backend):
    if not (har and schema_f and frontend and backend):
        return None
    fe = [d for d in (_kv(ln) for ln in frontend.read_text(encoding="utf-8", errors="replace").splitlines()) if d]
    by_ref, ref_meta = _parse_backend(backend.read_text(encoding="utf-8", errors="replace"))
    har_entries = _parse_har(har.read_text(encoding="utf-8", errors="replace"))
    schema = json.loads(schema_f.read_text(encoding="utf-8", errors="replace"))

    fg = _foreground_action(fe, har_entries)

    fg_entry = None
    for e in har_entries:
        if e["actionId"] == fg and e["resp"].get("validationRef"):
            fg_entry = e
            break
    if not fg_entry:
        for e in har_entries:
            r = e["resp"]
            if r.get("validationRef") and r.get("workflowRole") == "foreground-user-action":
                fg_entry, fg = e, e["actionId"]
                break
    if not fg_entry:
        return None

    interface = fg_entry["path"]
    ref = fg_entry["resp"].get("validationRef")
    meta = ref_meta.get(ref, {})
    if meta.get("source") == "diagnostic-replay" or meta.get("stage") == "replay":
        return None

    codes, code_fields = [], {}
    for code, fields in by_ref.get(ref, []):
        if fields.get("stage") == "replay" or fields.get("source") == "diagnostic-replay":
            continue
        codes.append(code)
        code_fields[code] = fields

    # module from a HAR schema/detail response tied to the foreground action.
    module = None
    for e in har_entries:
        data = e["resp"].get("data")
        if isinstance(data, dict) and data.get("module") and e["actionId"] and fg and e["actionId"].startswith(fg):
            module = data["module"]
            break
    if not module:
        for e in har_entries:
            data = e["resp"].get("data")
            if isinstance(data, dict) and data.get("module"):
                module = data["module"]
                break

    rootcauses = _apply_schema(schema, codes, code_fields, module)
    if not (module and interface and rootcauses):
        return None
    return "%s,%s,%s" % (module, interface, "、".join(rootcauses))


def _llm_fallback(har, schema_f, frontend, backend, others, question):
    blocks = []
    for label, f in (("frontend_log", frontend), ("backend_validation", backend), ("network.har", har), ("form_schema", schema_f)):
        if f is not None:
            blocks.append("===== %s (%s) =====\n%s" % (label, f.name, f.read_text(encoding="utf-8", errors="replace")[:30000]))
    for f, head in others:
        if f.suffix.lower() not in {".png", ".jpg", ".jpeg", ".zip", ".tar", ".gz"}:
            blocks.append("===== %s =====\n%s" % (f.name, head))
    prompt = (
        "题目与判定要求：\n%s\n\n证据材料（不含截图，截图为干扰项）：\n%s\n\n"
        "请选出唯一能跨文件闭环的完整前台作业流，按 form_schema 规则映射根因，"
        "只输出一行：缺陷模块,异常接口,根因关键词（多个根因用中文顿号、连接）。"
        % (question, "\n\n".join(blocks))
    )
    return skillio.clean_answer(ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=800, enable_thinking=True))


def main() -> None:
    skillio.read_stdin_args()
    files = skillio.list_files()
    har, schema_f, frontend, backend, others = _classify(files)

    answer = None
    try:
        answer = _solve(har, schema_f, frontend, backend)
    except Exception:
        answer = None

    if not answer:
        try:
            answer = _llm_fallback(har, schema_f, frontend, backend, others, skillio.question_text())
        except Exception:
            answer = ""

    skillio.emit(answer or "")


if __name__ == "__main__":
    main()
