from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


SELECT_SYSTEM = (
    "你是 DevPilot IDE 插件 FSE 数字人「小维」的资料检索选择器。"
    "给定一个用户问题和若干候选记录（来自 DB 历史记录或 Wiki 页面，每条带来源与时间），"
    "选出唯一一条最能正确、权威、贴合该问题【当前有效口径】的记录。"
    "判定原则：\n"
    "1) 只处理 DevPilot IDE 插件域；排除其它产品/插件的记录（如 CodeReviewBot、浏览器插件、插件商城问题单、SSO 旧版等）。\n"
    "2) 优先采用最新且仍然有效的处理口径；明确标注为旧版/归档/历史/试点/不再采用/曾经支持的记录一律不选。\n"
    "3) 当 DB 与 Wiki 就同一主题在时间线上冲突时，选时间更新且为正式（非试点）的那条；若问题就是『现在还能不能这么做』，要选纠正性的当前口径，而非被纠正的旧做法。\n"
    "4) 排除高风险错误做法：删除整个目录、关闭全部能力、直接重装、直接整体回滚、直接关闭企业代理/证书校验等表述通常是错误旧口径。\n"
    "只输出所选记录前面的编号数字，不要输出其它任何字符。"
)


def _pkg():
    return os.environ.get("PACKAGE_ID", "") or os.environ.get("packageId", "")


def _http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"X-Package-Id": _pkg()}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _find_file(*names):
    return skillio.find_first(*names)


def _load_source_access():
    f = _find_file("source_access.json")
    if not f:
        for p in skillio.list_files([".json"]):
            try:
                d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if "service_action_key_map" in d or "wiki_service" in d:
                return d
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _load_dialogs():
    # The dialog list is the file with an array of {id, question, ...}.
    best = None
    for p in skillio.list_files([".json"]):
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(d, list) and d and isinstance(d[0], dict) and "id" in d[0] and (
            "question" in d[0] or "user" in d[0]
        ):
            if "dialog" in p.name.lower() or best is None:
                best = d
    return best or []


def _db_candidates():
    f = _find_file("chat_history.db")
    if not f:
        dbs = [p for p in skillio.list_files() if p.suffix.lower() == ".db"]
        f = dbs[0] if dbs else None
    if not f:
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % f, uri=True)
    conn.row_factory = sqlite3.Row
    cands = []
    try:
        rows = conn.execute(
            """select m.message_id mid, m.content content, m.created_at created_at,
                      m.conversation_id conv, a.service_action_key key,
                      c.title title, c.tags tags
               from messages m
               join message_actions a on m.message_id = a.message_id
               left join conversations c on m.conversation_id = c.conversation_id"""
        ).fetchall()
        for r in rows:
            cands.append(
                {
                    "cid": "DB#" + r["mid"],
                    "src": "DB历史记录",
                    "time": (r["created_at"] or "").strip(),
                    "topic": (r["title"] or "") + " " + (r["tags"] or "") + " " + (r["conv"] or ""),
                    "reply": (r["content"] or "").strip(),
                    "key": r["key"],
                }
            )
    finally:
        conn.close()
    return cands


def _wiki_candidates(sa):
    wiki = (sa or {}).get("wiki_service", {})
    base = str(wiki.get("base_url", "")).rstrip("/")
    eps = wiki.get("endpoints", {})
    if not base:
        return []
    cands = []
    page_ids = []
    list_ep = eps.get("list_pages", "/api/wiki/pages")
    listing = _http_get_json(base + list_ep)
    items = None
    if isinstance(listing, list):
        items = listing
    elif isinstance(listing, dict):
        items = listing.get("data") or listing.get("pages") or listing.get("results")
    for it in items or []:
        if isinstance(it, dict) and it.get("id"):
            page_ids.append(it["id"])
    if not page_ids:
        for it in sa.get("pages", []) or wiki.get("pages", []):
            if isinstance(it, dict) and it.get("id"):
                page_ids.append(it["id"])
    get_tpl = eps.get("get_page", "/api/wiki/pages/{page_id}")
    for pid in page_ids:
        page = _http_get_json(base + get_tpl.replace("{page_id}", str(pid)))
        if not isinstance(page, dict):
            continue
        data = page.get("data") if isinstance(page.get("data"), dict) else page
        faqs = data.get("faqs") or data.get("faq") or []
        title = data.get("title", "")
        page_time = (data.get("updated_at") or data.get("page_updated_at") or "").strip()
        for faq in faqs:
            if not isinstance(faq, dict):
                continue
            cands.append(
                {
                    "cid": "WK#%s#%s" % (pid, faq.get("id", "")),
                    "src": "Wiki页面",
                    "time": page_time,
                    "topic": title + " " + str(faq.get("q", "")),
                    "reply": str(faq.get("a", "")).strip(),
                    "key": faq.get("service_action_key"),
                }
            )
    return cands


def _persona_phrase(item):
    user = item.get("user") or {}
    name = (user.get("name") or "").strip() if isinstance(user, dict) else ""
    if not name:
        name = (item.get("user_name") or "").strip()
    return ("您好%s总" % name) if name else "您好老师"


def _select(question, candidates):
    lines = []
    for i, c in enumerate(candidates):
        preview = c["reply"].replace("\n", " ")
        lines.append(
            "[%d] 来源:%s 时间:%s | 主题:%s | 答复:%s"
            % (
                i,
                c.get("src", "?"),
                c.get("time", "") or "未知",
                c["topic"].strip()[:80],
                preview[:240],
            )
        )
    prompt = (
        "用户问题：%s\n\n候选记录（按编号）：\n%s\n\n"
        "请按系统规则选出唯一最匹配且为当前有效口径的记录："
        "优先最新且正式的处理口径，排除旧版/归档/试点/他插件记录与高风险错误做法。"
        "只输出其编号数字。"
        % (question, "\n".join(lines))
    )
    out = ask(prompt, system=SELECT_SYSTEM, temperature=0.0, max_tokens=10, enable_thinking=False)
    m = re.search(r"\d+", skillio.clean_answer(out))
    if not m:
        return None
    idx = int(m.group(0))
    return candidates[idx] if 0 <= idx < len(candidates) else None


def _clean_field(text):
    return (text or "").replace("\n", " ").replace("=>", "").replace("|||", "").strip()


def main() -> None:
    skillio.read_stdin_args()
    sa = _load_source_access()
    key_map = (sa or {}).get("service_action_key_map", {})
    dialogs = _load_dialogs()

    candidates = _db_candidates() + _wiki_candidates(sa)

    results = []
    for item in dialogs:
        qid = item.get("id", "")
        question = item.get("question", "")
        persona = _persona_phrase(item)
        chosen = _select(question, candidates) if candidates else None
        if chosen:
            reply = _clean_field(chosen["reply"])
            action = _clean_field(key_map.get(chosen.get("key"), chosen.get("key") or ""))
        else:
            reply = ""
            action = ""
        results.append("%s=>%s|||%s|||%s" % (qid, persona, reply, action))

    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
