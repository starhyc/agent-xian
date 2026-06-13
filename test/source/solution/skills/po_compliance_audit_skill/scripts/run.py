"""
采购PO合规审计 Skill 入口
使用规则引擎+LLM理解审批内容
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
from datetime import datetime
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


def parse_date(date_str: str) -> datetime | None:
    """解析多种格式的日期"""
    date_str = date_str.strip()

    # 格式1: 2026年4月14日
    match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    # 格式2: Apr 15, 2026 或 April 15, 2026
    months = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    match = re.match(r'([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})', date_str)
    if match:
        month_str = match.group(1).lower()[:3]
        if month_str in months:
            return datetime(int(match.group(3)), months[month_str], int(match.group(2)))

    # 格式3: 2026-04-25 / 2026/04/25
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', date_str)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    return None


def parse_amount_threshold(rules_text: str) -> float:
    match = re.search(r"(?:金额|阈值|达到|超过|不低于|>=|≥)[^\d]{0,20}(\d{4,})", rules_text)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d{4,})\s*(?:CNY|元|人民币)", rules_text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else 50000.0


def is_completed_status(status: str) -> bool:
    """判断状态是否表示已结束"""
    status_lower = status.lower().strip()

    invalid_keywords = [
        '草稿', '评审中', '审批中', '待补件', '取消', '作废', '未结束',
        'draft', 'pending', 'review', 'approving', 'cancel', 'cancelled',
        'canceled', 'void', 'invalid',
    ]
    if any(keyword in status_lower for keyword in invalid_keywords):
        return False

    valid_keywords = [
        '已完成', 'completed', 'closed', 'paid', 'accepted',
        '已支付', '已验收', '已关闭', '已结案', 'done', 'settled', 'finished',
        '验收完成', '结案', '关闭'
    ]

    for keyword in valid_keywords:
        if keyword in status_lower:
            return True

    return False


def parse_service_items(service_str: str) -> list[str]:
    """解析服务项目列表"""
    items = re.split(r'[、,，;；/|\s]+', service_str)
    return [item.strip() for item in items if item.strip()]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def check_vendor_service_coverage(
    vendor_id: str,
    service_items: list[str],
    vendors_data: dict[str, dict]
) -> tuple[bool, str]:
    """检查供应商服务范围覆盖"""
    vendor = vendors_data.get(vendor_id)
    if not vendor:
        return False, f"供应商{vendor_id}不存在"

    service_scope = " ".join(str(vendor.get(key, "")) for key in ("service_scope", "scope", "business_scope", "service_items", "category_scope"))
    scope_items = parse_service_items(service_scope)

    not_covered = []
    for item in service_items:
        found = False
        for scope_item in scope_items:
            item_norm = normalize_text(item)
            scope_norm = normalize_text(scope_item)
            if item_norm and scope_norm and (item_norm in scope_norm or scope_norm in item_norm):
                found = True
                break
        if not found:
            not_covered.append(item)

    if not_covered:
        return False, f"服务项目[{', '.join(not_covered)}]不在供应商服务范围内"

    return True, "通过"


async def check_approval_coverage_with_llm(
    po_id: str,
    service_items: list[str],
    evidence_content: str
) -> tuple[bool, str]:
    """使用LLM判断审批是否明确覆盖PO的全部service_items"""
    service_items_str = "、".join(service_items)

    prompt = f"""你是一个采购合规审计专家。请判断审批邮件内容是否明确批准了指定PO的全部采购内容。

PO编号: {po_id}
采购内容: {service_items_str}

审批邮件内容:
---
{evidence_content}
---

判断标准:
1. 审批必须明确提及PO编号
2. 审批必须明确批准全部采购内容（不能只批准其中一项）
3. 审批中包含"考虑一下"、"先评估"、"待补材料"、"待确认"、"后续另行确认"等表示未完成审批的，不算有效审批
4. 审批其他PO的邮件不能覆盖当前PO

请只回答以下格式之一:
- "有效审批" - 表示审批明确覆盖并批准了全部采购内容
- "无效审批:原因" - 表示审批无效，简要说明原因"""

    load_dotenv()
    config = ModelConfig.from_env()
    client = ChatCompletionClient(config)

    messages = [
        {"role": "user", "content": prompt}
    ]

    try:
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        result = str(first_message(completion).get("content") or "").strip()

        log_debug(f"LLM审批判断结果: {result[:100]}")

        if result.startswith("有效审批"):
            return True, "LLM判断审批有效"
        elif result.startswith("无效审批:"):
            reason = result.replace("无效审批:", "").strip()
            return False, f"LLM判断:{reason}"
        else:
            return False, f"LLM判断异常:{result[:50]}"
    except Exception as exc:
        log_error(f"LLM调用失败: {exc}")
        return False, f"LLM调用失败:{exc}"


def extract_sender_email(evidence_content: str) -> str | None:
    patterns = [
        r'From:\s+[^<\n]*<([^>\s]+@[^>\s]+)>',
        r'From:\s*([^\s<]+@[^\s>]+)',
        r'发件人[:：]\s*[^<\n]*<([^>\s]+@[^>\s]+)>',
        r'发件人[:：]\s*([^\s<]+@[^\s>]+)',
        r'([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, evidence_content, flags=re.IGNORECASE)
        if match:
            return match.group(1).lower().strip(";,")
    return None


def extract_approval_date(evidence_content: str) -> datetime | None:
    patterns = [
        r'Date:\s*(.+)',
        r'日期[:：]\s*(.+)',
        r'审批日期[:：]\s*(.+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, evidence_content, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            parsed = parse_date(match.group(1))
            if parsed:
                return parsed
    return parse_date(evidence_content)


def is_vp_role(role: str) -> bool:
    role_norm = normalize_text(role)
    if "assistant" in role_norm or "助理" in role_norm:
        return False
    return role_norm == "vp" or "副总裁" in role_norm or "vicepresident" in role_norm


def approval_coverage_by_rules(po_id: str, service_items: list[str], evidence_content: str) -> tuple[bool, str]:
    content_norm = normalize_text(evidence_content)
    if normalize_text(po_id) not in content_norm:
        return False, f"审批内容未提及{po_id}"

    invalid_words = [
        "考虑一下", "先评估", "待补材料", "待确认", "另行确认", "后续确认",
        "只批准", "部分批准", "剩余项", "不批准", "驳回", "拒绝",
    ]
    if any(normalize_text(word) in content_norm for word in invalid_words):
        return False, "审批内容未明确完整批准"

    approve_words = ["批准", "同意", "approve", "approved", "approval", "agree"]
    if not any(word in content_norm for word in approve_words):
        return False, "未发现明确批准语义"

    missing = [item for item in service_items if normalize_text(item) not in content_norm]
    if missing:
        return False, f"审批未覆盖服务项:{','.join(missing)}"
    return True, "规则判断审批有效"


async def check_vp_approval(
    po_id: str,
    service_items: list[str],
    po_date: str,
    evidence_content: str,
    people_roles: dict[str, dict]
) -> tuple[bool, str]:
    """检查VP审批有效性"""
    log_debug(f"检查VP审批: {po_id}")

    # 1. 检查PO号是否在审批内容中
    if po_id not in evidence_content:
        log_debug(f"  审批内容未提及{po_id}")
        return False, f"审批内容未提及{po_id}"

    # 2. 提取发件人邮箱
    sender_email = extract_sender_email(evidence_content)
    if not sender_email:
        log_debug("  未找到发件人邮箱")
        return False, "未找到发件人邮箱"
    log_debug(f"  发件人邮箱: {sender_email}")

    # 3. 查找人员在people_roles中的记录
    person = None
    for p in people_roles.values():
        if p.get('email', '').lower() == sender_email:
            person = p
            break

    if not person:
        log_debug(f"  发件人{sender_email}不在人员角色表中")
        return False, f"发件人{sender_email}不在人员角色表中"

    # 4. 检查role是否为VP
    if not is_vp_role(person.get('role', '')):
        log_debug(f"  发件人角色为{person['role']}，不是VP")
        return False, f"发件人角色为{person['role']}，不是VP"

    # 5. 提取审批日期
    approval_date = extract_approval_date(evidence_content)
    if not approval_date:
        log_debug("  未找到审批日期")
        return False, "未找到审批日期"

    log_debug(f"  审批日期: {approval_date.date()}")

    # 6. 检查审批日期是否在VP有效期内
    valid_from = parse_date(person['valid_from'])
    valid_to = parse_date(person['valid_to'])

    if not valid_from or not valid_to:
        log_debug("  VP有效期解析失败")
        return False, "VP有效期解析失败"

    if approval_date < valid_from or approval_date > valid_to:
        log_debug(f"  审批日期不在VP有效期{valid_from.date()}至{valid_to.date()}内")
        return False, f"审批日期{approval_date.date()}不在VP有效期{valid_from.date()}至{valid_to.date()}内"

    # 7. 检查审批日期是否不晚于PO日期
    po_date_dt = parse_date(po_date)
    if po_date_dt and approval_date > po_date_dt:
        log_debug(f"  审批日期晚于PO日期")
        return False, f"审批日期{approval_date.date()}晚于PO日期{po_date_dt.date()}"

    # 8. 先用规则判断审批是否明确覆盖全部service_items，失败时再用LLM兜底
    rule_ok, rule_msg = approval_coverage_by_rules(po_id, service_items, evidence_content)
    if not rule_ok:
        llm_ok, llm_msg = await check_approval_coverage_with_llm(po_id, service_items, evidence_content)
        if not llm_ok:
            log_debug(f"  审批覆盖判断无效: {rule_msg}; {llm_msg}")
            return False, rule_msg

    log_debug("  VP审批有效")
    return True, "VP审批有效"


def natural_po_key(po_id: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", po_id)]


async def run_audit_async(data_dir: str) -> dict[str, Any]:
    """异步执行PO合规审计"""
    data_path = Path(data_dir)
    log_info(f"开始PO合规审计，数据目录: {data_dir}")
    audit_rules_text = ""
    audit_rules_path = data_path / "audit_rules.md"
    if audit_rules_path.exists():
        audit_rules_text = audit_rules_path.read_text(encoding="utf-8", errors="ignore")
    amount_threshold = parse_amount_threshold(audit_rules_text)
    log_info(f"金额阈值: {amount_threshold:g}")

    # 读取PO数据
    pos = {}
    with open(data_path / 'purchase_orders_raw.csv', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pos[row['po_id']] = row
    log_debug(f"读取PO数据: {len(pos)} 条")

    # 读取供应商数据
    vendors = {}
    with open(data_path / 'vendors.csv', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            vendors[row['vendor_id']] = row
    log_debug(f"读取供应商数据: {len(vendors)} 条")

    # 读取人员角色数据
    people = {}
    with open(data_path / 'people_roles.csv', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            people[row['person_id']] = row
    log_debug(f"读取人员角色数据: {len(people)} 条")

    # 读取审批证据索引
    evidence_index = {}
    with open(data_path / 'approval_evidence.csv', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            evidence_index[row['evidence_id']] = row
    log_debug(f"读取审批证据索引: {len(evidence_index)} 条")

    # 状态筛选
    status_filtered = []
    for po_id, po in pos.items():
        if is_completed_status(po['status']):
            status_filtered.append(po_id)
    log_info(f"状态筛选后: {len(status_filtered)} 条")

    # 金额筛选
    amount_filtered = []
    for po_id in status_filtered:
        po = pos[po_id]
        try:
            amount = float(str(po['amount_cny']).replace(",", ""))
            if amount >= amount_threshold:
                amount_filtered.append(po_id)
        except (ValueError, TypeError):
            pass
    log_info(f"金额筛选后: {len(amount_filtered)} 条")

    # 深审
    non_compliant = []
    deep_audited = []

    for idx, po_id in enumerate(amount_filtered, 1):
        po = pos[po_id]
        service_items = parse_service_items(po['service_items'])
        vendor_id = po['vendor_id']
        po_date = po['po_date']

        log_info(f"[{idx}/{len(amount_filtered)}] 审计PO: {po_id}")

        # 供应商服务范围校验
        vendor_ok, vendor_msg = check_vendor_service_coverage(
            vendor_id, service_items, vendors
        )
        if not vendor_ok:
            log_debug(f"  供应商校验失败: {vendor_msg}")

        # VP审批校验
        evidence_ids = po.get('evidence_ids', '').strip()
        vp_ok = False
        vp_msg = ""

        if evidence_ids:
            for ev_id in re.split(r'[,，;；\s]+', evidence_ids):
                ev_id = ev_id.strip()
                if ev_id in evidence_index:
                    ev = evidence_index[ev_id]
                    ev_path = (data_path / ev['file_path']).resolve()
                    if ev_path.exists() and ev_path.is_file() and ev_path.is_relative_to(data_path.resolve()):
                        content = ev_path.read_text(encoding='utf-8', errors='ignore')
                        vp_ok, vp_msg = await check_vp_approval(
                            po_id, service_items, po_date, content, people
                        )
                        if vp_ok:
                            break
        else:
            log_debug(f"  无审批证据")

        deep_audited.append(po_id)

        # 任一项不满足则不合规
        if not vendor_ok or not vp_ok:
            reason = []
            if not vendor_ok:
                reason.append(f"供应商:{vendor_msg}")
            if not vp_ok:
                reason.append(f"VP审批:{vp_msg}")
            non_compliant.append({
                'po_id': po_id,
                'reason': '; '.join(reason)
            })
            log_info(f"  不合规: {'; '.join(reason)}")
        else:
            log_info(f"  合规")

    # 排序输出
    non_compliant_ids = sorted([item['po_id'] for item in non_compliant], key=natural_po_key)
    log_info(f"审计完成，不合规PO: {len(non_compliant_ids)} 条")

    return {
        'answer': ','.join(non_compliant_ids),
        'total_pos': len(pos),
        'status_filtered': len(status_filtered),
        'amount_filtered': len(amount_filtered),
        'deep_audited': len(deep_audited),
        'non_compliant': len(non_compliant_ids),
        'details': non_compliant
    }


def main() -> None:
    """Skill入口"""
    raw = sys.stdin.read().strip() or "{}"
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        input_data = {}

    data_dir = input_data.get('data_dir', './采购PO合规审计/')

    result = asyncio.run(run_audit_async(data_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()