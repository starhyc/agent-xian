from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_csv_data(content: str) -> list[dict[str, str]]:
    """Parse CSV data."""
    records = []
    try:
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            records.append(dict(row))
    except Exception:
        pass
    return records


def parse_tab_data(content: str) -> list[dict[str, str]]:
    """Parse tab-separated data."""
    records = []
    lines = content.strip().split("\n")
    if len(lines) < 2:
        return records

    headers = [h.strip() for h in lines[0].split("\t")]
    for line in lines[1:]:
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) == len(headers):
            records.append({headers[i]: parts[i] for i in range(len(headers))})
    return records


def clean_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Clean and deduplicate records."""
    seen = set()
    cleaned = []
    duplicates = 0

    for record in records:
        # Create key for deduplication
        key_fields = ["id", "ID", "po_number", "PO_NUMBER", "order_id", "name", "Name"]
        key = None
        for field in key_fields:
            if field in record and record[field]:
                key = f"{field}:{record[field]}"
                break

        if key:
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)

        # Clean values
        cleaned_record = {}
        for k, v in record.items():
            if isinstance(v, str):
                v = v.strip()
                if v.lower() in ("", "null", "none", "n/a"):
                    v = ""
            cleaned_record[k] = v
        cleaned.append(cleaned_record)

    return cleaned, duplicates


def compute_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics."""
    if not records:
        return {"record_count": 0}

    stats = {
        "record_count": len(records),
        "fields": list(records[0].keys()),
        "field_count": len(records[0]),
    }

    # Find numeric fields
    numeric_fields = []
    for field in records[0].keys():
        try:
            values = [float(re.sub(r"[^\d.-]", "", str(r.get(field, "0")))) for r in records if r.get(field)]
            if values:
                numeric_fields.append(field)
                stats[f"{field}_sum"] = sum(values)
                stats[f"{field}_avg"] = sum(values) / len(values)
                stats[f"{field}_min"] = min(values)
                stats[f"{field}_max"] = max(values)
        except (ValueError, TypeError):
            pass

    stats["numeric_fields"] = numeric_fields

    # Categorical summaries
    categorical_fields = ["category", "Category", "supplier", "Supplier", "status", "Status"]
    for field in categorical_fields:
        if field in records[0]:
            counts = defaultdict(int)
            for r in records:
                if r.get(field):
                    counts[r[field]] += 1
            stats[f"{field}_distribution"] = dict(counts)

    return stats


def main() -> None:
    """Main entry point for the agent."""
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    task = payload.get("task", "Clean and summarize data")
    context_text = payload.get("context_text", "")
    agent_name = payload.get("agent_name", "data_cleaner_agent")

    # Try to parse data from context
    records = []
    if context_text:
        if "," in context_text.split("\n")[0]:
            records = parse_csv_data(context_text)
        else:
            records = parse_tab_data(context_text)

    # Clean data
    cleaned, duplicates = clean_records(records)

    # Compute statistics
    statistics = compute_statistics(cleaned)

    output = {
        "source": agent_name,
        "task": task,
        "original_records": len(records),
        "cleaned_records": len(cleaned),
        "duplicates_removed": duplicates,
        "statistics": statistics,
        "data_quality_score": len(cleaned) / len(records) if records else 1.0,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()