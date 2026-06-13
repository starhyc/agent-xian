from __future__ import annotations

import asyncio
import csv
from decimal import Decimal, InvalidOperation
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log(msg: str) -> None:
    print(f"[purchase_clean] {msg}", file=sys.stderr)


def parse_csv(content: str) -> list[dict[str, str]]:
    """Parse CSV content into list of dicts."""
    records = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        records.append(dict(row))
    return records


def load_all_data(base_dir: Path) -> dict[str, Any]:
    """Load all data files."""
    data = {}

    # Load PO data
    po_file = base_dir / "purchase_orders_raw.csv"
    if po_file.exists():
        data["po"] = parse_csv(po_file.read_text(encoding="utf-8-sig"))

    # Load vendors
    vendor_file = base_dir / "vendors.csv"
    if vendor_file.exists():
        data["vendors"] = parse_csv(vendor_file.read_text(encoding="utf-8-sig"))

    # Load category taxonomy
    category_file = base_dir / "category_taxonomy.csv"
    if category_file.exists():
        data["categories"] = parse_csv(category_file.read_text(encoding="utf-8-sig"))

    # Load attachment manifest
    attachment_file = base_dir / "attachment_manifest.csv"
    if attachment_file.exists():
        data["attachments"] = parse_csv(attachment_file.read_text(encoding="utf-8-sig"))

    # Load evidence index
    evidence_file = base_dir / "evidence_index.csv"
    if evidence_file.exists():
        data["evidence"] = parse_csv(evidence_file.read_text(encoding="utf-8-sig"))

    # Load queries
    queries_file = base_dir / "queries.csv"
    if queries_file.exists():
        data["queries"] = parse_csv(queries_file.read_text(encoding="utf-8-sig"))

    return data


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def first_value(record: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_amount(value: Any) -> Decimal | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None
    text = text.replace(",", "")
    text = re.sub(r"(人民币|元|cny|rmb|¥|￥|\s)", "", text, flags=re.IGNORECASE)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def parse_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "人民币" in text or "CNY" in text or "RMB" in text or "¥" in text or "￥" in text:
        return "CNY"
    if "USD" in text or "美元" in text or "$" in text:
        return "USD"
    if "EUR" in text or "欧元" in text:
        return "EUR"
    return text


def read_attachment_content(base_dir: Path, record: dict[str, str]) -> str:
    path_value = first_value(record, ["file_path", "path", "filepath", "attachment_path", "evidence_path"])
    parts = [json.dumps(record, ensure_ascii=False)]
    if path_value:
        path = (base_dir / path_value).resolve()
        if path.exists() and path.is_file() and path.is_relative_to(base_dir.resolve()):
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass
    return "\n".join(parts)


def enrich_attachments(data: dict[str, Any], base_dir: Path) -> list[dict[str, str]]:
    attachments = [dict(item) for item in data.get("attachments", [])]
    evidence_rows = [dict(item) for item in data.get("evidence", [])]
    evidence_by_id = {}
    for row in evidence_rows:
        row_id = first_value(row, ["attachment_id", "evidence_id", "id"])
        if row_id:
            evidence_by_id[row_id] = row

    if not attachments and evidence_rows:
        attachments = evidence_rows
    else:
        for attachment in attachments:
            row_id = first_value(attachment, ["attachment_id", "evidence_id", "id"])
            if row_id in evidence_by_id:
                merged = {**evidence_by_id[row_id], **attachment}
                attachment.clear()
                attachment.update(merged)

    for attachment in attachments:
        attachment["_content"] = read_attachment_content(base_dir, attachment)
    return attachments


def build_vendor_lookup(vendors: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Build lookup index for vendors."""
    lookup = {}
    for v in vendors:
        vendor_id = v.get("vendor_id", "")
        lookup[vendor_id] = {
            "vendor_id": vendor_id,
            "legal_name": v.get("legal_name", ""),
            "tax_id": v.get("tax_id", ""),
            "business_scope": v.get("business_scope", ""),
            "service_scope": v.get("service_scope", ""),
            "brand_name": v.get("brand_name", ""),
            "english_name": v.get("english_name", ""),
            "city": v.get("city", ""),
        }
        # Also index by legal_name and brand_name
        if v.get("legal_name"):
            lookup[v["legal_name"]] = lookup[vendor_id]
        if v.get("brand_name"):
            lookup[v["brand_name"]] = lookup[vendor_id]
        if v.get("tax_id"):
            lookup[v["tax_id"]] = lookup[vendor_id]
        if v.get("english_name"):
            lookup[v["english_name"]] = lookup[vendor_id]
    return lookup


def build_category_lookup(categories: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Build lookup index for categories."""
    lookup = {}
    for c in categories:
        code = c.get("category_code", "")
        lookup[code] = {
            "category_code": code,
            "category_name": c.get("category_name", ""),
            "definition": c.get("definition", ""),
            "boundary_note": c.get("boundary_note", ""),
        }
    return lookup


def is_valid_attachment(attachment: dict[str, str]) -> bool:
    """判断附件是否为有效证据"""
    status = normalize_text(attachment.get("status", ""))
    attachment_type = normalize_text(attachment.get("attachment_type", ""))
    content = normalize_text(attachment.get("_content", ""))

    invalid_words = ["作废", "已作废", "废弃", "旧版", "历史版本", "错po", "错误po", "wrongpo", "old", "invalid", "previous"]
    if any(normalize_text(word) in status or normalize_text(word) in attachment_type for word in invalid_words):
        return False
    if any(normalize_text(word) in content for word in invalid_words):
        return False
    
    return True


def get_attachments_for_po(
    po_id: str,
    attachments: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """获取 PO 对应的附件，按类型分组"""
    result = {
        "invoice": [],  # 发票
        "contract": [],  # 合同
        "chat": [],  # 聊天截图
        "quote": [],  # 报价单
        "comparison": [],  # 比价记录
        "other": [],
    }
    
    for att in attachments:
        if att.get("po_id", "") != po_id:
            continue
        if not is_valid_attachment(att):
            continue
        
        att_type = att.get("attachment_type", "").lower()
        if "发票" in att_type or "invoice" in att_type:
            result["invoice"].append(att)
        elif "合同" in att_type or "contract" in att_type:
            result["contract"].append(att)
        elif "聊天" in att_type or "chat" in att_type:
            result["chat"].append(att)
        elif "报价" in att_type or "quote" in att_type:
            result["quote"].append(att)
        elif "比价" in att_type or "comparison" in att_type:
            result["comparison"].append(att)
        else:
            result["other"].append(att)
    
    return result


def find_vendor_candidates(text: str, vendor_lookup: dict[str, Any]) -> list[str]:
    norm = normalize_text(text)
    candidates = []
    for key, vendor in vendor_lookup.items():
        if not key:
            continue
        if normalize_text(str(key)) and normalize_text(str(key)) in norm:
            candidates.append(vendor["vendor_id"])
    return list(dict.fromkeys(candidates))


def attachment_vendor_ids(attachments: dict[str, list[dict[str, str]]], vendor_lookup: dict[str, Any]) -> list[str]:
    ids = []
    for group in ("invoice", "contract"):
        for attachment in attachments.get(group, []):
            fields = [
                first_value(attachment, ["vendor_id"]),
                first_value(attachment, ["vendor_name", "legal_name", "supplier_name"]),
                first_value(attachment, ["tax_id", "credit_code", "统一社会信用代码"]),
                attachment.get("_content", ""),
            ]
            for field in fields:
                ids.extend(find_vendor_candidates(field, vendor_lookup))
    return list(dict.fromkeys(ids))


def resolve_vendor_id(po: dict[str, str], vendor_lookup: dict[str, Any], attachments: dict[str, list[dict[str, str]]]) -> str | None:
    system_vendor = first_value(po, ["vendor_id", "supplier_id"])
    system_valid = system_vendor in vendor_lookup
    evidence_ids = attachment_vendor_ids(attachments, vendor_lookup)

    if evidence_ids:
        if len(evidence_ids) == 1:
            evidence_id = evidence_ids[0]
            if not system_valid or system_vendor == evidence_id:
                return evidence_id
            return evidence_id
        return None
    return system_vendor if system_valid else None


def category_text(category: dict[str, Any]) -> str:
    return " ".join(str(category.get(key, "")) for key in ("category_code", "category_name", "definition", "boundary_note", "keywords", "service_scope"))


def find_category_candidates(text: str, category_lookup: dict[str, Any]) -> list[str]:
    norm = normalize_text(text)
    candidates = []
    for code, category in category_lookup.items():
        if not code:
            continue
        tokens = [code, category.get("category_name", ""), category.get("definition", "")]
        for token in tokens:
            token_norm = normalize_text(str(token))
            if token_norm and (token_norm in norm or norm in token_norm):
                candidates.append(code)
                break
    return list(dict.fromkeys(candidates))


def contract_category_ids(attachments: dict[str, list[dict[str, str]]], category_lookup: dict[str, Any]) -> list[str]:
    ids = []
    for attachment in attachments.get("contract", []):
        text = " ".join([
            first_value(attachment, ["category_code", "category_name", "service_scope", "scope"]),
            attachment.get("_content", ""),
        ])
        ids.extend(find_category_candidates(text, category_lookup))
    return list(dict.fromkeys(ids))


def resolve_category_code(po: dict[str, str], category_lookup: dict[str, Any], attachments: dict[str, list[dict[str, str]]]) -> str | None:
    system_category = first_value(po, ["category_code", "category"])
    system_valid = system_category in category_lookup
    contract_ids = contract_category_ids(attachments, category_lookup)

    if contract_ids:
        if len(contract_ids) == 1:
            contract_id = contract_ids[0]
            return contract_id if (not system_valid or system_category == contract_id) else contract_id
        if system_valid and system_category in contract_ids:
            return system_category
        return None
    return system_category if system_valid else None


def extract_amounts_from_text(text: str) -> list[Decimal]:
    amounts = []
    for match in re.finditer(r"(?:金额|价税合计|合计|总额|amount)?\s*[:：]?\s*(?:人民币|CNY|RMB|¥|￥)?\s*[-]?\d[\d,]*(?:\.\d+)?", text, flags=re.IGNORECASE):
        amount = parse_amount(match.group(0))
        if amount is not None:
            amounts.append(amount)
    return amounts


def invoice_amounts(attachments: dict[str, list[dict[str, str]]]) -> list[Decimal]:
    amounts = []
    for attachment in attachments.get("invoice", []):
        amount = parse_amount(first_value(attachment, ["amount", "invoice_amount", "total_amount", "amount_cny"]))
        if amount is not None:
            amounts.append(amount)
            continue
        extracted = extract_amounts_from_text(attachment.get("_content", ""))
        if len(extracted) == 1:
            amounts.append(extracted[0])
    return amounts


def attachment_currencies(attachments: dict[str, list[dict[str, str]]]) -> list[str]:
    currencies = []
    for attachment in attachments.get("invoice", []):
        currency = parse_currency(first_value(attachment, ["currency", "invoice_currency"]))
        if not currency:
            content = attachment.get("_content", "")
            currency = parse_currency(content)
        if currency:
            currencies.append(currency)
    return list(dict.fromkeys(currencies))


def resolve_amount_and_currency(po: dict[str, str], attachments: dict[str, list[dict[str, str]]]) -> tuple[Decimal | None, str | None, str | None]:
    system_amount = parse_amount(first_value(po, ["amount_raw", "amount", "amount_cny", "total_amount"]))
    system_currency = parse_currency(first_value(po, ["currency", "currency_code"]))
    inv_amounts = invoice_amounts(attachments)
    inv_currencies = attachment_currencies(attachments)

    if system_currency and system_currency != "CNY":
        return None, None, "non-CNY system currency"
    if any(currency != "CNY" for currency in inv_currencies):
        return None, None, "non-CNY invoice currency"
    currency = system_currency or ("CNY" if "CNY" in inv_currencies else "")
    if not currency:
        return None, None, "currency not confirmed"

    if system_amount is not None:
        if inv_amounts and any(system_amount > invoice_amount for invoice_amount in inv_amounts):
            return None, None, "system amount greater than invoice amount"
        return system_amount, currency, None

    unique_invoice_amounts = list(dict.fromkeys(inv_amounts))
    if len(unique_invoice_amounts) == 1:
        return unique_invoice_amounts[0], currency, None
    return None, None, "amount not uniquely confirmed"


def validate_po_by_rules(
    po_record: dict[str, str],
    vendor_lookup: dict[str, Any],
    category_lookup: dict[str, Any],
    po_attachments: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    vendor_id = resolve_vendor_id(po_record, vendor_lookup, po_attachments)
    if not vendor_id:
        return {"valid": False, "reason": "vendor not uniquely confirmed"}

    category_code = resolve_category_code(po_record, category_lookup, po_attachments)
    if not category_code:
        return {"valid": False, "reason": "category not uniquely confirmed"}

    amount, currency, error = resolve_amount_and_currency(po_record, po_attachments)
    if error or amount is None or currency != "CNY":
        return {"valid": False, "reason": error or "invalid amount/currency"}

    return {
        "valid": True,
        "vendor_id": vendor_id,
        "category_code": category_code,
        "amount": amount,
        "currency": currency,
        "reason": "rules",
    }


def call_llm_validate_po(
    po_record: dict[str, str],
    vendor_lookup: dict[str, Any],
    category_lookup: dict[str, Any],
    po_attachments: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """使用大模型验证 PO 记录的供应商归一、品类归一和金额有效性"""
    import asyncio as _asyncio

    system_prompt = """你是一个采购数据清洗专家。

给定一条 PO 记录、供应商主数据、品类主数据和有效附件，你需要判断：
1. 该 PO 是否能归一到唯一的 (vendor_id, category_code)
2. 该 PO 的金额是否有效
3. 该 PO 是否应该计入汇总

证据链优先级（从高到低）：
1. 发票：可校验金额、币种、供应商法定名称、统一社会信用代码
2. 合同：可辅助确认供应商主体和服务范围/采购品类，但不能覆盖系统金额/发票金额
3. 聊天截图/报价单/比价记录：只能提示用途，不能补金额

清洗规则：
1. 有效附件：关联当前 PO、未作废、不是旧版、不是错 PO 的附件
2. 供应商校验：vendor_id 缺失、无效，或与有效发票/合同中的供应商法定主体冲突时，需重新确认
3. 品类校验：category_code 缺失、无效，或与有效合同服务范围明显冲突时，需重新确认
4. 金额校验：系统金额清楚且有效附件未否定时，使用系统金额；系统金额缺失时，只有匹配的有效发票可补
5. 系统金额 > 对应有效发票金额 → 不计入
6. 币种校验：只统计 CNY；USD/EUR 等非 CNY 不计入，不做汇率换算
7. 聊天截图、报价单、比价记录只能提示用途，不能补金额、不能覆盖系统金额

返回 JSON 格式：
{
  "valid": true/false,
  "vendor_id": "V0001",
  "category_code": "COMPUTE_SERVICE",
  "amount": 12345,
  "currency": "CNY",
  "reason": "原因说明"
}"""

    # 构建附件信息
    invoice_info = []
    for inv in po_attachments.get("invoice", []):
        invoice_info.append({
            "type": inv.get("attachment_type", ""),
            "amount": inv.get("amount", ""),
            "currency": inv.get("currency", ""),
            "vendor_name": inv.get("vendor_name", ""),
            "tax_id": inv.get("tax_id", ""),
        })
    
    contract_info = []
    for con in po_attachments.get("contract", []):
        contract_info.append({
            "type": con.get("attachment_type", ""),
            "vendor_name": con.get("vendor_name", ""),
            "service_scope": con.get("service_scope", ""),
        })

    user_prompt = f"""PO 记录：
{json.dumps(po_record, ensure_ascii=False, indent=2)}

供应商主数据（部分）：
{json.dumps(list(vendor_lookup.values())[:30], ensure_ascii=False, indent=2)}

品类主数据：
{json.dumps(list(category_lookup.values()), ensure_ascii=False, indent=2)}

有效发票附件：
{json.dumps(invoice_info, ensure_ascii=False, indent=2)}

有效合同附件：
{json.dumps(contract_info, ensure_ascii=False, indent=2)}

判断该 PO 是否有效。返回 JSON。"""

    async def _call_llm():
        load_dotenv()
        config = ModelConfig.from_env()
        client = ChatCompletionClient(config)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        return first_message(completion).get("content", "")

    try:
        content = _asyncio.run(_call_llm())
        return json.loads(content)
    except Exception as exc:
        log(f"LLM call failed: {exc}")
        return {"valid": False, "reason": f"API error: {exc}"}


def process_po_batch(
    po_records: list[dict[str, str]],
    vendor_lookup: dict[str, Any],
    category_lookup: dict[str, Any],
    attachments: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """批量处理 PO 记录"""
    results = []
    for po in po_records:
        po_id = po.get("po_id", "")
        po_attachments = get_attachments_for_po(po_id, attachments)
        result = validate_po_by_rules(po, vendor_lookup, category_lookup, po_attachments)
        result["po_id"] = po_id
        results.append(result)
    return results


def compute_query_results(
    validated_pos: list[dict[str, Any]],
    queries: list[dict[str, str]],
) -> dict[str, Decimal]:
    """根据 queries.csv 计算每个查询的金额汇总"""
    # Build index by (vendor_id, category_code)
    index: dict[tuple, list[Decimal]] = {}
    for po in validated_pos:
        if not po.get("valid"):
            continue
        key = (po.get("vendor_id", ""), po.get("category_code", ""))
        if key not in index:
            index[key] = []
        amount = po.get("amount", Decimal(0))
        if amount:
            index[key].append(amount if isinstance(amount, Decimal) else Decimal(str(amount)))

    # Compute sums for each query
    results = {}
    for query in queries:
        query_id = query.get("query_id", "")
        vendor_id = query.get("vendor_id", "")
        category_code = query.get("category_code", "")

        key = (vendor_id, category_code)
        amounts = index.get(key, [])
        total = sum(amounts, Decimal(0))
        results[query_id] = total

    return results


def query_sort_key(query: dict[str, str]) -> tuple[int, Any]:
    query_id = str(query.get("query_id", ""))
    match = re.search(r"\d+", query_id)
    if match:
        return (0, int(match.group(0)))
    return (1, query_id)


def format_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return str(int(amount))
    return format(amount.normalize(), "f")


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    data_dir = payload.get("data_dir", "")
    log(f"data_dir: {data_dir}")
    
    if not data_dir:
        log("No data_dir provided")
        print(json.dumps({"error": "No data_dir provided"}, ensure_ascii=False))
        return

    base_dir = Path(data_dir)
    if not base_dir.exists():
        log(f"Directory not found: {data_dir}")
        print(json.dumps({"error": f"Directory not found: {data_dir}"}, ensure_ascii=False))
        return

    # Load all data
    log("Loading data files...")
    data = load_all_data(base_dir)
    
    if not data.get("po"):
        log("No PO data found")
        print(json.dumps({"error": "No PO data found"}, ensure_ascii=False))
        return
    if not data.get("vendors"):
        log("No vendor data found")
        print(json.dumps({"error": "No vendor data found"}, ensure_ascii=False))
        return
    if not data.get("categories"):
        log("No category data found")
        print(json.dumps({"error": "No category data found"}, ensure_ascii=False))
        return
    if not data.get("queries"):
        log("No queries found")
        print(json.dumps({"error": "No queries found"}, ensure_ascii=False))
        return

    log(f"Loaded: {len(data['po'])} POs, {len(data['vendors'])} vendors, {len(data['categories'])} categories, {len(data['queries'])} queries")

    # Build lookups
    log("Building lookups...")
    vendor_lookup = build_vendor_lookup(data["vendors"])
    category_lookup = build_category_lookup(data["categories"])
    attachments = enrich_attachments(data, base_dir)

    # Process PO records with deterministic rules
    log(f"Processing {len(data['po'])} PO records with rules...")
    validated_pos = process_po_batch(
        data["po"], vendor_lookup, category_lookup, attachments
    )
    log(f"Validated {len(validated_pos)} PO records")

    # Compute query results
    log("Computing query results...")
    query_results = compute_query_results(validated_pos, data["queries"])

    # Format output
    log("Formatting output...")
    answers = []
    for query in sorted(data["queries"], key=query_sort_key):
        query_id = query.get("query_id", "")
        answers.append(format_amount(query_results.get(query_id, Decimal(0))))

    log(f"Completed: {len(answers)} results")
    print(json.dumps({
        "answer": ",".join(answers),
        "total_queries": len(data["queries"]),
        "valid_pos": sum(1 for po in validated_pos if po.get("valid")),
        "invalid_pos": sum(1 for po in validated_pos if not po.get("valid")),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()