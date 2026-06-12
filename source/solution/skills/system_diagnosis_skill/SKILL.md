---
name: system_diagnosis_skill
description: Analyze system logs and diagnose issues.
---

# system_diagnosis_skill

Analyze system logs and diagnose issues.

## Input

```json
{
  "base_dir": "path/to/logs"
}
```
### 字段说明

- `base_dir`: 日志文件根路径

## Output

```json
{
  "answer": "缺陷模块,异常接口,根因关键词1、根因关键词2、..."
}
```

## Usage

Call `skill_run` with the logs directory.
