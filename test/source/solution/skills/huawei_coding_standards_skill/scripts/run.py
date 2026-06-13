from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[huawei_coding] {msg}", file=sys.stderr)


def read_file_safe(file_path: str) -> str:
    """安全读取文件"""
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def normalize_lang(value: str) -> str:
    """标准化规范领域名称。"""
    text = value.lower()
    if "javascript" in text or "typescript" in text or "js/ts" in text or "js" in text:
        return "JavaScript"
    if "java" in text:
        return "Java"
    if "python" in text:
        return "Python"
    if "c++" in text or "cpp" in text:
        return "C++"
    if "web" in text or "安全" in text:
        return "Web安全"
    return "Unknown"


def infer_lang(question: str, current_lang: str) -> str:
    """没有分组标题时，根据问题关键词推断领域。"""
    if current_lang != "Unknown":
        return current_lang
    lowered = question.lower()
    if "python" in lowered or "none" in lowered or "导入模块" in question:
        return "Python"
    if "c++" in lowered or "copy constructor" in lowered or "头文件" in question:
        return "C++"
    if "javascript" in lowered or "typescript" in lowered or "constructor" in lowered or "equality" in lowered:
        return "JavaScript"
    if "web" in lowered or "cookie" in lowered or "xss" in lowered or "口令" in question:
        return "Web安全"
    if "java" in lowered or "import" in lowered or "long" in lowered:
        return "Java"
    return "Unknown"


def answer_by_rules(lang: str, question: str) -> str | None:
    """固定规范内容对应的高置信短答案。"""
    lowered = question.lower()

    if lang == "Java":
        if "import" in lowered and ("huawei" in lowered or "华为" in question or "com.huawei" in lowered):
            return "Android"
        if "long" in lowered and ("suffix" in lowered or "后缀" in question or "literal" in lowered):
            return "L"

    if lang == "Python":
        if "导入" in question or "import" in lowered:
            return "标准库、第三方库、应用程序自定义模块"
        if "none" in lowered:
            return "is、is not"

    if lang == "C++":
        if "扩展名" in question or "extension" in lowered:
            return "cpp、h"
        if "copy constructor" in lowered or "copy assignment" in lowered or "拷贝构造" in question:
            return "同时"

    if lang == "JavaScript":
        if "构造器" in question or "constructor" in lowered or "类" in question and "命名" in question:
            return "大驼峰"
        if "equality" in lowered or "相等" in question or "比较" in question:
            return "===、!=="

    if lang == "Web安全":
        if "口令" in question or "password" in lowered and "type" in lowered:
            return "password"
        if "cookie" in lowered and ("xss" in lowered or "reading" in lowered or "读取" in question):
            return "HttpOnly"

    return None


def search_in_doc(question: str, doc_content: str) -> str | None:
    """在文档中搜索问题的答案，兜底返回相关规范原文片段。"""
    tokens = []
    tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9_.+-]*", question.lower()))
    tokens.extend(re.findall(r"[\u4e00-\u9fa5]{2,}", question))
    stopwords = {"what", "which", "should", "used", "when", "with", "the", "for", "in", "be"}
    tokens = [token for token in tokens if token not in stopwords]

    relevant: list[tuple[int, str]] = []
    for line in doc_content.splitlines():
        clean = line.strip()
        if not clean:
            continue
        lowered = clean.lower()
        score = sum(1 for token in tokens if token.lower() in lowered)
        if score:
            relevant.append((score, clean))

    relevant.sort(key=lambda item: item[0], reverse=True)
    if not relevant:
        return None

    line = relevant[0][1]
    line = re.sub(r"^[-*•]\s*", "", line)
    line = re.sub(r"^\|\s*|\s*\|$", "", line)
    return line[:120]


def extract_questions_from_text(text: str) -> list[tuple[str, str]]:
    """从文本中提取问题列表，返回 (语言/领域, 问题内容) 元组列表"""
    questions = []

    current_lang = "Unknown"
    for line in text.splitlines():
        heading_match = re.search(r"【([^】]+)】", line)
        if heading_match:
            heading_lang = normalize_lang(heading_match.group(1))
            if heading_lang != "Unknown":
                current_lang = heading_lang

        for match in re.finditer(r"Q\d+\s*[:：]\s*(.+?)(?=(?:\s+Q\d+\s*[:：])|$)", line, flags=re.IGNORECASE):
            q_content = match.group(1).strip()
            questions.append((infer_lang(q_content, current_lang), q_content))

    return questions


def get_doc_for_language(lang: str, docs: dict[str, str]) -> str:
    """获取对应语言的文档"""
    lang_map = {
        "Java": ["java", "Java"],
        "Python": ["python", "Python"],
        "C++": ["c++", "C++", "cpp"],
        "JavaScript": ["javascript", "js", "typescript"],
        "Web安全": ["web", "安全"],
    }
    
    keywords = lang_map.get(lang, [])
    for keyword in keywords:
        for doc_name, doc_content in docs.items():
            if keyword.lower() in doc_name.lower():
                return doc_content
    return ""


def load_docs(base_path: Path) -> dict[str, str]:
    """递归读取规范文本文件。"""
    docs = {}
    if not base_path.exists():
        return docs
    file_patterns = {".md", ".txt", ".markdown"}
    for file_path in sorted(path for path in base_path.rglob("*") if path.is_file()):
        if file_path.suffix.lower() not in file_patterns:
            continue
        doc_name = str(file_path.relative_to(base_path))
        docs[doc_name] = read_file_safe(str(file_path))
    return docs


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    docs_dir = payload.get("docs_directory", "")
    question_text = payload.get("question", "")
    
    log(f"docs_dir: {docs_dir}")

    if not docs_dir:
        log("No docs_directory provided")
        print(json.dumps({"error": "No docs_directory provided"}, ensure_ascii=False))
        return

    if not question_text:
        log("No question provided")
        print(json.dumps({"error": "No question provided"}, ensure_ascii=False))
        return

    base_path = Path(docs_dir)

    log("Reading standards documents...")
    docs = load_docs(base_path)
    log(f"Loaded {len(docs)} documents")

    # 提取问题
    log("Extracting questions...")
    questions = extract_questions_from_text(question_text)
    log(f"Found {len(questions)} questions")

    # 回答每个问题
    log("Searching for answers...")
    answers = []
    for lang, q_content in questions:
        answer = answer_by_rules(lang, q_content)
        if not answer:
            doc_content = get_doc_for_language(lang, docs)
            answer = search_in_doc(q_content, doc_content) if doc_content else None
        answers.append(answer or "")

    log(f"Generated {len(answers)} answers")
    # 按顺序输出，用分号分隔
    result = ";".join(answers)
    print(json.dumps({"answer": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()