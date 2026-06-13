from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

import requests


def parse_api_spec(context: str) -> dict[str, Any]:
    """Parse API specification from context text."""
    spec = {
        "base_url": "http://127.0.0.1:18081",
        "endpoint": "/",
        "method": "GET",
        "expected_status": 200,
    }

    # Extract URL
    url_match = re.search(r"(https?://[^\s]+)", context)
    if url_match:
        spec["base_url"] = url_match.group(1).rstrip("/")

    # Extract endpoint path
    path_match = re.search(r"(?:endpoint|path|接口)[：:]\s*(/\S*)", context)
    if path_match:
        spec["endpoint"] = path_match.group(1)

    # Extract method
    method_match = re.search(r"(?:method|方法)[：:]\s*(GET|POST|PUT|DELETE|PATCH)", context, re.IGNORECASE)
    if method_match:
        spec["method"] = method_match.group(1).upper()

    # Extract expected status
    status_match = re.search(r"(?:status|状态)[：:]\s*(\d{3})", context)
    if status_match:
        spec["expected_status"] = int(status_match.group(1))

    return spec


def make_test_request(spec: dict[str, Any]) -> dict[str, Any]:
    """Make an HTTP request to test the API."""
    url = f"{spec['base_url'].rstrip('/')}/{spec['endpoint'].lstrip('/')}"

    start_time = time.time()
    try:
        response = requests.request(
            method=spec["method"],
            url=url,
            timeout=30,
        )
        elapsed_ms = int((time.time() - start_time) * 1000)

        return {
            "success": True,
            "url": url,
            "method": spec["method"],
            "status_code": response.status_code,
            "response_body": response.text[:5000],
            "latency_ms": elapsed_ms,
            "expected_status": spec["expected_status"],
            "status_matches": response.status_code == spec["expected_status"],
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Request timeout",
            "url": url,
            "latency_ms": int((time.time() - start_time) * 1000),
        }
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "Connection failed - is the server running?",
            "url": url,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "url": url,
        }


def main() -> None:
    """Main entry point for the agent."""
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    task = payload.get("task", "Test API endpoint")
    context_text = payload.get("context_text", "")
    agent_name = payload.get("agent_name", "api_tester_agent")

    # Parse API spec from context
    spec = parse_api_spec(context_text)

    # Make request
    result = make_test_request(spec)

    output = {
        "source": agent_name,
        "task": task,
        "api_spec": spec,
        "test_result": result,
        "response_valid": result.get("status_matches", False) if result.get("success") else False,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()