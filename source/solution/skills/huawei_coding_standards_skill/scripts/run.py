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


def extract_keywords(question: str) -> list[str]:
    """从问题中提取关键词"""
    # 移除常见的问题前缀
    question = re.sub(r"^(Q\d+:?\s*)", "", question, flags=re.IGNORECASE)
    # 提取英文单词和中文词组
    keywords = []
    # 英文关键词
    keywords.extend(re.findall(r"[A-Za-z][\w\s]+?(?=\s+(?:should|be|in|with|or|and|after|before))", question))
    # 中文关键词
    keywords.extend(re.findall(r"[\u4e00-\u9fa5]+", question))
    return [k.strip() for k in keywords if k.strip()]


def search_in_doc(question: str, doc_content: str) -> str | None:
    """在文档中搜索问题的答案"""
    question_lower = question.lower()
    
    # 提取问题的核心概念
    core_concepts = []
    
    # 提取引号内的内容（通常是具体要求）
    quotes = re.findall(r'"([^"]+)"', question)
    core_concepts.extend(quotes)
    
    # 提取问号前的内容（问题核心）
    core_match = re.search(r"([^?]+)\?", question)
    if core_match:
        core = core_match.group(1).strip()
        # 移除常见开头
        core = re.sub(r"^(what|which|how|should|when|where|why|is|are)\s+", "", core, flags=re.IGNORECASE)
        core_concepts.append(core)
    
    # 在文档中搜索相关段落
    lines = doc_content.split("\n")
    relevant_lines = []
    
    for line in lines:
        line_lower = line.lower()
        score = 0
        for concept in core_concepts:
            if concept.lower() in line_lower:
                score += len(concept)
        if score > 0:
            relevant_lines.append((score, line))
    
    # 按相关性排序
    relevant_lines.sort(key=lambda x: x[0], reverse=True)
    
    # 查找答案（通常在列表项或表格中）
    for _, line in relevant_lines[:20]:
        line = line.strip()
        if not line:
            continue
        
        # 检查是否是列表项
        list_match = re.search(r"^[-*•]\s+(.+)", line)
        if list_match:
            return list_match.group(1).strip()
        
        # 检查是否是表格行
        table_match = re.search(r"^\|\s*(.+?)\s*\|", line)
        if table_match:
            return table_match.group(1).strip()
        
        # 检查是否包含冒号后的内容
        colon_match = re.search(r":\s*(.+?)(?:\.|$)", line)
        if colon_match:
            return colon_match.group(1).strip()
    
    # 如果没找到精确答案，返回最相关的行
    if relevant_lines:
        line = relevant_lines[0][1].strip()
        # 清理格式
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"^\|\s*", "", line)
        return line[:200]
    
    return None


def extract_questions_from_text(text: str) -> list[tuple[str, str]]:
    """从文本中提取问题列表，返回 (语言/领域, 问题内容) 元组列表"""
    questions = []
    
    # 按语言/领域分割
    sections = re.split(r"\n(?=【(Java|Python|C\+\+|JS/TS|JavaScript|Web安全|Web安全规范))", text)
    
    current_lang = "Unknown"
    for section in sections:
        # 检测语言/领域
        lang_match = re.search(r"【(.+?)】", section)
        if lang_match:
            current_lang = lang_match.group(1)
            # 标准化语言名称
            if "Java" in current_lang and "Script" not in current_lang:
                current_lang = "Java"
            elif "Python" in current_lang:
                current_lang = "Python"
            elif "C++" in current_lang:
                current_lang = "C++"
            elif "JavaScript" in current_lang or "JS" in current_lang:
                current_lang = "JavaScript"
            elif "Web安全" in current_lang:
                current_lang = "Web安全"
        
        # 提取问题
        q_matches = re.findall(r"(Q\d+)\s*[:：]\s*(.+?)(?=\n|$)", section)
        for q_num, q_content in q_matches:
            questions.append((current_lang, q_content.strip()))
    
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

    # 读取所有 md 文档
    log("Reading markdown files...")
    docs = {}
    for md_file in base_path.glob("*.md"):
        doc_name = md_file.name
        doc_content = read_file_safe(str(md_file))
        docs[doc_name] = doc_content
    log(f"Loaded {len(docs)} documents")

    # 提取问题
    log("Extracting questions...")
    questions = extract_questions_from_text(question_text)
    log(f"Found {len(questions)} questions")

    # 回答每个问题
    log("Searching for answers...")
    answers = []
    for lang, q_content in questions:
        # 获取对应语言的文档
        doc_content = get_doc_for_language(lang, docs)
        
        if doc_content:
            answer = search_in_doc(q_content, doc_content)
            if answer:
                answers.append(answer)
            else:
                answers.append("")
        else:
            answers.append("")

    log(f"Generated {len(answers)} answers")
    # 按顺序输出，用分号分隔
    result = ";".join(answers)
    print(json.dumps({"answer": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()