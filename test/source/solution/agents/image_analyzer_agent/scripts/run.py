from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any


def load_image(image_path: str) -> str | None:
    """Load image and return base64 string."""
    try:
        path = Path(image_path)
        if not path.exists():
            return None
        with path.open("rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def analyze_image_content(image_data: str, task: str, categories: list[str]) -> dict[str, Any]:
    """Analyze image content (placeholder for actual vision model)."""
    # This would normally call a vision model API
    # For now, return a structured response indicating the analysis
    return {
        "category": "unknown",
        "confidence": 0.0,
        "description": "Image analysis requires a vision model API call",
        "objects": [],
        "note": "Use this agent's output to construct a prompt for a vision-capable model",
        "recommended_prompt": f"Analyze this image and classify it into one of these categories: {', '.join(categories)}. {task}",
    }


def main() -> None:
    """Main entry point for the agent."""
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    task = payload.get("task", "Analyze and classify this image")
    context_text = payload.get("context_text", "")
    agent_name = payload.get("agent_name", "image_analyzer_agent")

    # Parse categories from context if available
    categories = []
    if context_text:
        # Extract potential category names
        import re
        categories = re.findall(r"[\u4e00-\u9fa5a-zA-Z]{2,}(?=[\s,，]|$)", context_text)
        categories = list(set(categories))[:10]

    # Analyze
    result = analyze_image_content("", task, categories)

    output = {
        "source": agent_name,
        "task": task,
        "categories": categories,
        "analysis": result,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()