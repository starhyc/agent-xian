"""
Prompt Learning Skill - 图片分类任务
读取训练集学习分类规则，对验证集进行推理
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
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


def load_image_as_base64(image_path: Path) -> str | None:
    """加载图片并转为 base64"""
    try:
        if not image_path.exists():
            return None
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def load_training_data(train_dir: Path) -> list[dict[str, Any]]:
    """加载训练集图片和标签"""
    training_data = []

    if not train_dir.exists():
        return training_data

    image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
    for img_file in sorted(train_dir.iterdir()):
        if img_file.is_file() and img_file.suffix.lower() in image_extensions:
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

    image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
    for img_file in sorted(val_dir.iterdir()):
        if img_file.is_file() and img_file.suffix.lower() in image_extensions:
            validation_data.append({
                "image_path": str(img_file),
                "image_name": img_file.name
            })

    return validation_data


async def learn_from_training_set(training_data: list[dict[str, Any]]) -> dict[str, Any]:
    """从训练集学习分类规则"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    examples_text = []
    for item in training_data:
        examples_text.append(f"- {item['image_name']}: {item['label']}")

    learning_prompt = f"""你是一个图像分类专家，需要学习以下训练样本的分类规则：

训练样本（共 {len(training_data)} 个）：
{chr(10).join(examples_text)}

任务描述：
- 如果在地上或者人手上出现长筒手套，返回 PASS
- 如果未看到人手的部分，返回 NOT_INVOLVED
- 如果看到所有的人手都没有长筒手套，则返回 FAIL

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


async def classify_image(image_base64: str, learned_rules: str) -> str:
    """使用学习到的规则对图片进行分类"""
    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    classification_prompt = f"""你是一个图像分类专家，根据以下规则判断图片中是否存在长筒手套：

判断规则：
{learned_rules}

请仔细观察图片，判断：
- 如果在地上或者人手上出现长筒手套，返回 PASS
- 如果未看到人手的部分，返回 NOT_INVOLVED
- 如果看到所有的人手都没有长筒手套，则返回 FAIL

只输出一个词：PASS、FAIL 或 NOT_INVOLVED"""

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
        result = str(first_message(completion).get("content") or "").upper().strip()

        if "PASS" in result:
            return "PASS"
        elif "FAIL" in result:
            return "FAIL"
        elif "NOT_INVOLVED" in result:
            return "NOT_INVOLVED"
        else:
            return "NOT_INVOLVED"
    except Exception as exc:
        log_error(f"图片分类失败: {exc}")
        return "NOT_INVOLVED"


async def run_prompt_learning(base_dir: str) -> dict[str, Any]:
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

    # 学习分类规则
    learned = await learn_from_training_set(training_data)
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
            prediction = await classify_image(image_base64, learned_rules)
        else:
            prediction = "NOT_INVOLVED"

        results.append(f"{idx}{prediction}")
        log_info(f"  [{idx}/{len(validation_data)}] {img_path.name} -> {prediction}")

    answer = ",".join(results)
    log_info(f"推理完成，共 {len(results)} 条结果")

    return {
        "answer": answer,
        "total": len(validation_data),
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
    if not base_dir:
        print(json.dumps({"error": "No base_dir provided"}, ensure_ascii=False))
        return

    result = asyncio.run(run_prompt_learning(base_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()