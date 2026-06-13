"""
Prompt Learning Skill - 图片分类任务
读取训练集学习分类规则，对验证集进行推理
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def log_debug(msg: str) -> None:
    """调试日志"""
    print(f"[DEBUG] {msg}", file=sys.stderr)


def log_info(msg: str) -> None:
    """信息日志"""
    print(f"[INFO] {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    """错误日志"""
    print(f"[ERROR] {msg}", file=sys.stderr)


def load_image_as_base64(image_path: Path) -> str | None:
    """加载图片并转为 base64"""
    try:
        if not image_path.exists():
            return None
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def natural_key(path: Path | str) -> list[Any]:
    text = path.name if isinstance(path, Path) else str(path)
    parts = re.split(r"(\d+)", text)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def image_index(image_name: str, fallback: int) -> int:
    match = re.search(r"\d+", image_name)
    return int(match.group(0)) if match else fallback


def extract_labels(training_data: list[dict[str, Any]]) -> list[str]:
    labels = []
    for item in training_data:
        label = str(item.get("label", "")).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def majority_label(training_data: list[dict[str, Any]], labels: list[str]) -> str:
    counts = {label: 0 for label in labels}
    for item in training_data:
        label = str(item.get("label", "")).strip()
        if label in counts:
            counts[label] += 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def parse_label_from_output(output: str, labels: list[str]) -> str | None:
    clean = output.strip()
    for label in labels:
        if clean == label:
            return label
    upper = clean.upper()
    for label in labels:
        if upper == label.upper():
            return label
    tokens = re.findall(r"[A-Za-z_]+|[\u4e00-\u9fa5]+", clean)
    for token in tokens:
        for label in labels:
            if token.upper() == label.upper():
                return label
    return None


def task_description_from_question(question: str) -> str:
    if not question:
        return ""
    match = re.search(r"【任务描述】(.*?)(?:数据集：|【返回格式】|$)", question, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return question[:1200]


def load_training_data(train_dir: Path) -> list[dict[str, Any]]:
    """加载训练集图片和标签"""
    training_data = []

    if not train_dir.exists():
        return training_data

    for img_file in sorted(train_dir.iterdir(), key=natural_key):
        if img_file.is_file() and img_file.suffix.lower() in IMAGE_EXTENSIONS:
            label_file = img_file.with_suffix(".txt")
            label = ""
            if label_file.exists():
                label = label_file.read_text(encoding="utf-8").strip()

            training_data.append({
                "image_path": str(img_file),
                "image_name": img_file.name,
                "label": label
            })

    return training_data


def load_validation_data(val_dir: Path) -> list[dict[str, Any]]:
    """加载验证集图片"""
    validation_data = []

    if not val_dir.exists():
        return validation_data

    for img_file in sorted(val_dir.iterdir(), key=natural_key):
        if img_file.is_file() and img_file.suffix.lower() in IMAGE_EXTENSIONS:
            validation_data.append({
                "image_path": str(img_file),
                "image_name": img_file.name
            })

    return validation_data


def sample_training_data(training_data: list[dict[str, Any]], max_per_label: int = 3) -> list[dict[str, Any]]:
    by_label: dict[str, list[dict[str, Any]]] = {}
    for item in training_data:
        by_label.setdefault(str(item.get("label", "")), []).append(item)
    sampled = []
    for label in sorted(by_label):
        sampled.extend(by_label[label][:max_per_label])
    return sampled


async def learn_from_training_set(training_data: list[dict[str, Any]], question: str, labels: list[str]) -> dict[str, Any]:
    """从训练集学习分类规则"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    examples_text = []
    for item in sample_training_data(training_data, max_per_label=5):
        examples_text.append(f"- {item['image_name']}: {item['label']}")
    task_description = task_description_from_question(question)

    learning_prompt = f"""你是一个图像分类专家，需要学习以下训练样本的分类规则：

训练样本（共 {len(training_data)} 个）：
{chr(10).join(examples_text)}

任务描述：
{task_description}

合法标签：{", ".join(labels)}

请总结出对该任务最有效的判断规则，简洁明了地描述。"""

    messages = [{"role": "user", "content": learning_prompt}]

    try:
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        content = str(first_message(completion).get("content") or "")
        log_debug(f"学习完成，学习到的规则: {content[:100]}...")
        return {
            "learned_rules": content,
            "examples_summary": f"共 {len(training_data)} 个训练样本"
        }
    except Exception as exc:
        log_error(f"学习失败: {exc}")
        return {
            "learned_rules": f"学习失败: {exc}",
            "examples_summary": f"共 {len(training_data)} 个训练样本"
        }


async def classify_image(image_base64: str, learned_rules: str, question: str, labels: list[str], fallback_label: str) -> str:
    """使用学习到的规则对图片进行分类"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    task_description = task_description_from_question(question)
    classification_prompt = f"""你是一个图像分类专家，请根据任务描述和学习规则对图片分类。

任务描述：
{task_description}

判断规则：
{learned_rules}

合法标签：{", ".join(labels)}

只输出一个合法标签，不要解释。"""

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": classification_prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                }
            ]
        }
    ]

    try:
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        result = str(first_message(completion).get("content") or "").strip()
        return parse_label_from_output(result, labels) or fallback_label
    except Exception as exc:
        log_error(f"图片分类失败: {exc}")
        return fallback_label


async def run_prompt_learning(base_dir: str, question: str = "") -> dict[str, Any]:
    """执行提示词学习"""
    base_path = Path(base_dir)
    train_dir = base_path / "训练集"
    val_dir = base_path / "验证集"

    log_info(f"开始提示词学习，数据目录: {base_dir}")

    # 加载训练集
    training_data = load_training_data(train_dir)
    if not training_data:
        return {"error": "No training data found", "answer": ""}

    log_info(f"加载训练集: {len(training_data)} 张图片")
    labels = extract_labels(training_data)
    if not labels:
        return {"error": "No labels found", "answer": ""}
    fallback_label = majority_label(training_data, labels) or labels[0]

    # 学习分类规则
    learned = await learn_from_training_set(training_data, question, labels)
    learned_rules = learned.get("learned_rules", "")

    # 加载验证集
    validation_data = load_validation_data(val_dir)
    if not validation_data:
        return {"error": "No validation data found", "answer": ""}

    log_info(f"加载验证集: {len(validation_data)} 张图片")

    # 对验证集进行推理
    results = []

    for idx, item in enumerate(validation_data, 1):
        img_path = Path(item["image_path"])
        image_base64 = load_image_as_base64(img_path)

        if image_base64:
            prediction = await classify_image(image_base64, learned_rules, question, labels, fallback_label)
        else:
            prediction = fallback_label

        output_index = image_index(item["image_name"], idx)
        results.append(f"{output_index}{prediction}")
        log_info(f"  [{idx}/{len(validation_data)}] {img_path.name} -> {prediction}")

    answer = ",".join(results)
    log_info(f"推理完成，共 {len(results)} 条结果")

    return {
        "answer": answer,
        "total": len(validation_data),
        "labels": labels,
        "learned_rules": learned_rules[:500] if learned_rules else ""
    }


def main() -> None:
    """主函数"""
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    base_dir = payload.get("base_dir", "")
    question = payload.get("question", "")
    if not base_dir:
        print(json.dumps({"error": "No base_dir provided"}, ensure_ascii=False))
        return

    result = asyncio.run(run_prompt_learning(base_dir, question))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()