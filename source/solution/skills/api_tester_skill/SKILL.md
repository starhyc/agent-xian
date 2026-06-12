---
name: api_tester_skill
description: Execute API test cases and return failed case IDs.
---

# api_tester_skill

Execute API test cases.

## Input

```json
{
  "base_url": "http://127.0.0.1:18081",
  "test_cases_path": "path/to/test_cases.json"
}
```

## Output

```json
{
  "answer": "TC009,TC011,TC014,TC015,TC016,TC020"
}
```

## Usage

Call `skill_run` to execute test cases.
