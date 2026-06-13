---
name: date_parser_skill
description: Parse and normalize various date formats from customer service messages.
---

# date_parser_skill

Parse dates from customer service messages.

## Input

```json
{
  "text": "raw customer messages",
  "base_date": "2026-05-06"
}
```

## Output

```json
{
  "dates": ["2026-12-21", "2026-03-15"],
  "count": 2
}
```

## Usage

Call `skill_run` with the text content.
