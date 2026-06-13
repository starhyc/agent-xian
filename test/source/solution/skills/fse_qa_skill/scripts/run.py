"""
IDE插件FSE数字人问答 Skill
根据人设、历史记录和Wiki资料回答问题
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log_debug(msg: str) -> None:
    """调试日志"""
    print(f"[DEBUG] {msg}", file=sys.stderr)


def log_info(msg: str) -> None:
    """信息日志"""
    print(f"[INFO] {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    """错误日志"""
    print(f"[ERROR] {msg}", file=sys.stderr)


def call_wiki_api(
    endpoint: str,
    base_url: str = "http://127.0.0.1:18089",
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用Wiki API"""
    url = f"{base_url}{endpoint}"
    try:
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method.upper())
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        log_error(f"Wiki API调用失败: {exc}")
        return {"error": str(exc)}


def api_paths(source_access: dict[str, Any]) -> dict[str, str]:
    paths = source_access.get("api_paths") or source_access.get("wiki_service", {}).get("api_paths") or {}
    return {
        "list": paths.get("list") or paths.get("list_pages") or "/api/wiki/pages",
        "search": paths.get("search") or "/api/wiki/search",
        "get_page": paths.get("get_page") or paths.get("page") or "/api/wiki/pages/{page_id}",
        "update": paths.get("update") or paths.get("submit_update") or "/api/wiki/update",
    }


def unwrap_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "pages", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = unwrap_items(value)
                if nested:
                    return nested
    return []


def get_wiki_page(page_id: str, base_url: str = "http://127.0.0.1:18089", source_access: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取Wiki页面详情"""
    endpoint = api_paths(source_access or {})["get_page"].format(page_id=urllib.parse.quote(str(page_id)))
    data = call_wiki_api(endpoint, base_url)
    if isinstance(data.get("data"), dict):
        return data["data"]
    return data


def query_chat_history(db_path: Path) -> list[dict[str, Any]]:
    """查询chat_history.db获取历史回复"""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        log_debug(f"查询chat_history.db: {db_path}")

        cursor.execute("""
            SELECT m.message_id, m.content, ma.service_action_key
            FROM messages m
            LEFT JOIN message_actions ma ON m.message_id = ma.message_id
            WHERE m.role = 'assistant'
            ORDER BY m.created_at DESC
        """)

        results = []
        for row in cursor.fetchall():
            results.append({
                "message_id": row["message_id"],
                "content": row["content"],
                "service_action_key": row["service_action_key"]
            })

        conn.close()
        log_debug(f"从chat_history获取到 {len(results)} 条记录")
        return results
    except Exception as exc:
        log_error(f"查询chat_history失败: {exc}")
        return []


def query_chat_history_generic(db_path: Path) -> list[dict[str, Any]]:
    """尽量从 SQLite 任意表结构中抽取历史答复原文和动作 key。"""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        results = query_chat_history(db_path)

        seen = {item.get("content", "") for item in results}
        for table in tables:
            columns = [row[1] for row in cursor.execute(f"PRAGMA table_info({table})")]
            content_cols = [col for col in columns if col.lower() in {"content", "message", "reply", "answer", "response"}]
            action_cols = [col for col in columns if "service_action_key" in col.lower() or "action_key" in col.lower()]
            question_cols = [col for col in columns if col.lower() in {"question", "query", "user_message", "title", "intent"}]
            if not content_cols:
                continue
            selected = list(dict.fromkeys(content_cols + action_cols + question_cols))
            sql = f"SELECT {', '.join(selected)} FROM {table} LIMIT 1000"
            for row in cursor.execute(sql):
                content = str(row[content_cols[0]] or "")
                if not content or content in seen:
                    continue
                seen.add(content)
                match_text = " ".join(str(row[col] or "") for col in selected)
                results.append({
                    "message_id": f"{table}:{len(results)}",
                    "content": content,
                    "match_text": match_text,
                    "service_action_key": str(row[action_cols[0]] or "") if action_cols else "",
                })
        conn.close()
        return results
    except Exception as exc:
        log_error(f"泛化查询chat_history失败: {exc}")
        return query_chat_history(db_path)


def normalize_text(text: str) -> str:
    """标准化文本用于匹配"""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def calculate_match_score(pattern: str, text: str) -> float:
    """计算匹配分数"""
    pattern_norm = normalize_text(pattern.lower())
    text_norm = normalize_text(text.lower())

    if pattern_norm in text_norm:
        return 1.0

    keywords = pattern_norm.split()
    if len(keywords) <= 1:
        keywords = re.findall(r"[A-Za-z0-9_.+-]{2,}", pattern_norm)
        for chunk in re.findall(r"[\u4e00-\u9fa5]{2,}", pattern_norm):
            keywords.extend(chunk[index:index + 2] for index in range(0, max(len(chunk) - 1, 0)))
    matched = sum(1 for kw in keywords if len(kw) > 2 and kw in text_norm)
    if not matched:
        matched = sum(1 for kw in keywords if len(kw) == 2 and kw in text_norm)
    if keywords:
        return matched / len(keywords)
    return 0.0


def find_best_chat_match(question: str, chat_history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从chat_history中找到最佳匹配"""
    best_match = None
    best_score = 0.0

    for item in chat_history:
        content = item.get("content", "")
        score = calculate_match_score(question, content)
        if score > best_score:
            best_score = score
            best_match = item

    if best_score >= 0.3:
        log_debug(f"chat_history最佳匹配分数: {best_score:.2f}")
        return best_match
    log_debug(f"chat_history无足够匹配 (最高分: {best_score:.2f})")
    return None


def find_best_wiki_match(question: str, wiki_pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从Wiki中找到最佳匹配"""
    best_match = None
    best_score = 0.0

    for page in wiki_pages:
        overview = page.get("overview", "")
        score = calculate_match_score(question, overview)
        if score > best_score:
            best_score = score
            best_match = page

    if best_score >= 0.2:
        log_debug(f"Wiki最佳匹配分数: {best_score:.2f}")
        return best_match
    log_debug(f"Wiki无足够匹配 (最高分: {best_score:.2f})")
    return None


def resolve_service_action(service_action_key: str, key_map: dict[str, str]) -> str:
    """解析service_action"""
    action = key_map.get(service_action_key, "标准答复")
    log_debug(f"解析service_action_key: {service_action_key} -> {action}")
    return action


def collect_wiki_candidates(source_access: dict[str, Any], wiki_base_url: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 list/search/wiki_sources 获取页面，再以 get_page FAQ 详情生成候选。"""
    paths = api_paths(source_access)
    pages: list[dict[str, Any]] = []
    pages.extend(source_access.get("wiki_sources", []) if isinstance(source_access.get("wiki_sources"), list) else [])

    listed = call_wiki_api(paths["list"], wiki_base_url)
    pages.extend(unwrap_items(listed))

    for question in questions:
        q_text = str(question.get("question", ""))
        search_endpoint = paths["search"]
        sep = "&" if "?" in search_endpoint else "?"
        searched = call_wiki_api(f"{search_endpoint}{sep}q={urllib.parse.quote(q_text)}", wiki_base_url)
        pages.extend(unwrap_items(searched))

    page_by_id: dict[str, dict[str, Any]] = {}
    for page in pages:
        page_id = str(page.get("id") or page.get("page_id") or page.get("pageId") or page.get("slug") or "")
        if page_id:
            page_by_id[page_id] = {**page_by_id.get(page_id, {}), **page}

    candidates = []
    for page_id, page in page_by_id.items():
        detail = get_wiki_page(page_id, wiki_base_url, source_access)
        faqs = detail.get("faqs") or detail.get("faq") or detail.get("items") or []
        if isinstance(faqs, dict):
            faqs = list(faqs.values())
        for faq in faqs if isinstance(faqs, list) else []:
            if not isinstance(faq, dict):
                continue
            reply = str(faq.get("a") or faq.get("answer") or "")
            if not reply:
                continue
            match_text = " ".join(str(part or "") for part in [
                page.get("title"), page.get("overview"), detail.get("title"), detail.get("overview"),
                faq.get("q"), faq.get("question"), reply,
            ])
            candidates.append({
                "source": "wiki",
                "page_id": page_id,
                "reply": reply,
                "match_text": match_text,
                "service_action_key": str(faq.get("service_action_key") or faq.get("action_key") or ""),
            })
    return candidates


def collect_db_candidates(chat_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for item in chat_history:
        reply = str(item.get("content") or "")
        if not reply:
            continue
        candidates.append({
            "source": "chat_history",
            "reply": reply,
            "match_text": str(item.get("match_text") or reply),
            "service_action_key": str(item.get("service_action_key") or ""),
        })
    return candidates


def choose_candidate(question_text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    prefer_wiki = any(word in question_text for word in ("最新", "Wiki", "wiki", "更新", "资料"))
    prefer_db = any(word in question_text for word in ("历史", "曾经", "上次", "已有记录", "处理记录"))
    scored = []
    for candidate in candidates:
        score = calculate_match_score(question_text, candidate.get("match_text", ""))
        if prefer_wiki and candidate.get("source") == "wiki":
            score += 0.2
        if prefer_db and candidate.get("source") == "chat_history":
            score += 0.2
        scored.append((score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def maybe_submit_wiki_update(source_access: dict[str, Any], wiki_base_url: str, question: dict[str, Any], reply: str, service_action: str) -> None:
    if "更新Wiki" not in service_action:
        return
    endpoint = api_paths(source_access)["update"]
    body = {"question_id": question.get("id", ""), "question": question.get("question", ""), "suggested_reply": reply}
    result = call_wiki_api(endpoint, wiki_base_url, method="POST", body=body)
    if result.get("error"):
        log_error(f"Wiki更新建议提交失败: {result.get('error')}")


def generate_persona_phrase(user_name: str) -> str:
    """生成人设化称呼"""
    if user_name and user_name.strip():
        return f"您好{user_name}总"
    return "您好老师"


def format_answer_item(
    question_id: str,
    persona_phrase: str,
    reply: str,
    service_action: str
) -> str:
    """格式化答案项"""
    reply = reply.replace("=>", "").replace("|||", "")
    service_action = service_action.replace("=>", "").replace("|||", "")
    return f'{question_id}=>{persona_phrase}|||{reply}|||{service_action}'


async def call_llm_judge(question: str, wiki_pages: list[dict[str, Any]]) -> tuple[str, str]:
    """使用大模型判断问题并生成回复"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    system_prompt = """你是一个IDE插件FSE数字人"小维"，负责处理DevPilot IDE插件问题。

## 人设
- 温柔：语气柔和，使用敬语
- 积极解决：尽力解答和跟进
- 主动服务：必要时提醒用户补充信息

## 规则
1. reply必须与提供的内容原文完全一致，不得翻译、改写或删减
2. service_action必须从以下选项中选择：标准答复、阻止高风险操作、要求补充定位信息、收集提单必填信息、技术问题升级二线、情绪风险升级运营代表或SRE Leader、建群协同每1小时同步、发送未响应提醒、拒绝无结论关单、合并沟通同一用户多个问题单、反馈差评与重复问题、更新Wiki为最新指导、过滤无关记录
3. 涉及删除目录、重装、回滚等高风险操作应阻止

## Wiki页面概览
"""

    for page in wiki_pages:
        system_prompt += f"- {page.get('title', '')}: {page.get('overview', '')}\n"

    user_prompt = f"""用户问题：{question}

请根据Wiki页面内容和人设规则，判断：
1. 哪个Wiki页面的内容最适合回答这个问题
2. 生成合适的回复（必须使用Wiki原文，不得改写）
3. 选择合适的service_action

返回JSON格式：
{{"selected_title": "页面标题", "reply": "回复内容（使用Wiki原文）", "service_action": "服务动作"}}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        content = str(first_message(completion).get("content") or "{}")
        result = json.loads(content)
        return result.get("reply", "请提供更多信息以便进一步定位问题。"), result.get("service_action", "要求补充定位信息")
    except Exception as exc:
        log_error(f"LLM调用失败: {exc}")
        return "请提供更多信息以便进一步定位问题。", "要求补充定位信息"


async def process_questions(
    questions: list[dict[str, Any]],
    chat_history: list[dict[str, Any]],
    source_access: dict[str, Any],
    wiki_base_url: str
) -> tuple[list[str], dict[str, int]]:
    """处理所有问题"""
    service_action_key_map = source_access.get("service_action_key_map", {})
    matched_from = {"chat_history": 0, "wiki": 0, "fallback": 0}
    candidates = collect_db_candidates(chat_history)
    candidates.extend(collect_wiki_candidates(source_access, wiki_base_url, questions))

    answers = []

    for idx, q in enumerate(questions, 1):
        question_id = q.get("id", "")
        user_obj = q.get("user") if isinstance(q.get("user"), dict) else {}
        user_name = user_obj.get("name") or q.get("user_name") or q.get("name") or ""
        question_text = q.get("question") or q.get("query") or q.get("content") or ""

        log_info(f"[{idx}/{len(questions)}] 处理问题: {question_id}")

        persona_phrase = generate_persona_phrase(user_name)
        selected = choose_candidate(str(question_text), candidates)
        if selected:
            reply = selected.get("reply", "")
            service_action_key = selected.get("service_action_key", "")
            service_action = resolve_service_action(service_action_key, service_action_key_map)
            source = selected.get("source", "fallback")
            matched_from[source] = matched_from.get(source, 0) + 1
            maybe_submit_wiki_update(source_access, wiki_base_url, q, reply, service_action)
            log_debug(f"  来源: {source}")
        else:
            reply = ""
            service_action = resolve_service_action("", service_action_key_map)
            matched_from["fallback"] += 1
            log_debug("  未找到合法来源")

        answer_item = format_answer_item(question_id, persona_phrase, reply, service_action)
        answers.append(answer_item)
        log_info(f"  完成: {question_id}")

    return answers, matched_from


async def run_fse_qa_async(data_dir: str) -> dict[str, Any]:
    """异步执行FSE问答"""
    data_path = Path(data_dir)

    log_info(f"开始FSE问答，数据目录: {data_dir}")

    # 读取persona
    persona_path = data_path / "persona.md"
    persona = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""
    log_debug(f"读取persona: {len(persona)} 字符")

    # 读取source_access
    source_access_path = data_path / "source_access.json"
    with open(source_access_path, encoding="utf-8") as f:
        source_access = json.load(f)
    log_debug(f"读取source_access完成")

    # 读取问题列表
    questions_path = data_path / "dialog_tests_complex.json"
    with open(questions_path, encoding="utf-8") as f:
        questions = json.load(f)
    log_info(f"加载 {len(questions)} 个问题")

    # 读取chat_history
    chat_history_path = data_path / "chat_history.db"
    chat_history = query_chat_history_generic(chat_history_path)

    # Wiki服务地址
    wiki_base_url = source_access.get("wiki_service", {}).get("base_url", "http://127.0.0.1:18089")

    # 处理所有问题
    answers, matched_from = await process_questions(
        questions, chat_history, source_access, wiki_base_url
    )

    log_info(f"FSE问答完成，匹配来源: {matched_from}")

    return {
        "answer": json.dumps(answers, ensure_ascii=False),
        "total_questions": len(questions),
        "matched_from": matched_from
    }


def main() -> None:
    """Skill入口"""
    raw = sys.stdin.read().strip() or "{}"
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        input_data = {}

    data_dir = input_data.get('data_dir', './IDE插件FSE/')

    result = asyncio.run(run_fse_qa_async(data_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()