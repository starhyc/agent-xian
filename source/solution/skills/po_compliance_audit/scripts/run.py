from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask


SYSTEM = (
    "你是严谨的采购 PO 合规审计员。只依据给定资料判断【单个 PO】是否合规，"
    "严格执行：供应商服务范围必须全覆盖该 PO 的全部 service_items；"
    "VP 审批必须由审批日 role 为 VP、在有效期内、审批日期不晚于 po_date 的人按发件邮箱匹配，"
    "且附件正文明确批准当前 PO 的全部 service_items。"
    "Director/Manager/VP Assistant/过期 VP、只批部分项、待确认/先评估/另行确认、审批其他 PO 的邮件都不算有效。"
    "只输出 COMPLIANT 或 NON_COMPLIANT 一个词。"
)

# Status semantics (bilingual). 结束/已完成/已支付/已验收/已关闭/已结案 => 有效。
_ENDED = [
    "已完成", "已支付", "已验收", "已关闭", "已结案", "已结束", "已收货", "已交付",
    "completed", "complete", "closed", "paid", "accepted", "settled", "done", "finished",
]
_NOT_ENDED = [
    "草稿", "评审中", "审批中", "待补件", "待审", "取消", "作废", "已取消", "已作废", "暂停",
    "draft", "under_review", "under review", "reviewing", "pending", "approving",
    "cancelled", "canceled", "void", "voided", "rejected", "on_hold",
]


def _decode(p):
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return ""


def _read_csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def _find(name_substr):
    for p in skillio.list_files([".csv", ".md", ".txt"]):
        if name_substr in p.name.lower():
            return p
    return None


def _status_ended(status):
    s = (status or "").strip().lower()
    if not s:
        return False
    if any(tok.lower() in s for tok in _NOT_ENDED):
        return False
    return any(tok.lower() in s for tok in _ENDED)


def _threshold(text):
    # Look for "... >= NNNNN CNY" / "NNNNN CNY" thresholds; default 50000.
    m = re.search(r"(?:>=|大于等于|不低于|达到|超过)\s*([0-9][0-9,]{3,})", text)
    if not m:
        m = re.search(r"([0-9][0-9,]{3,})\s*CNY", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except Exception:
            pass
    return 50000


def _attachments_for(po_id, ev_field, evidence_rows, by_name):
    """Collect attachment bodies relevant to a PO via evidence index."""
    wanted_ids = set()
    for tok in re.split(r"[;,/\s]+", ev_field or ""):
        tok = tok.strip()
        if tok:
            wanted_ids.add(tok)
    texts = []
    for row in evidence_rows:
        eid = (row.get("evidence_id") or "").strip()
        related = row.get("related_po_ids") or ""
        if eid in wanted_ids or po_id in related:
            fp = (row.get("file_path") or "").strip()
            base = Path(fp).name
            path = by_name.get(base)
            if path is None:
                path = skillio.find_first(base)
            if path is not None:
                body = _decode(path)
                if body:
                    texts.append("----- 附件 %s (%s) -----\n%s" % (eid, fp, body))
    return texts


def _vendor_scope(vendors_rows, vendor_id, vendor_name):
    for r in vendors_rows:
        if (r.get("vendor_id") or "").strip() == (vendor_id or "").strip():
            return r.get("service_scope") or ""
    for r in vendors_rows:
        if (r.get("vendor_name") or "").strip() == (vendor_name or "").strip():
            return r.get("service_scope") or ""
    return ""


def _audit_one(po, scope, people_text, attach_texts, rules_text):
    prompt = (
        "审计规则：\n%s\n\n"
        "待审 PO：\n%s\n\n"
        "该供应商(%s / %s)的 service_scope：\n%s\n\n"
        "people_roles（判断 VP 身份与有效期）：\n%s\n\n"
        "审批往来附件正文：\n%s\n\n"
        "请判断该 PO 是否合规：\n"
        "1) service_items 是否【全部】落在上面的 service_scope 内（按语义，任一关键采购项不在范围内即不合规）；\n"
        "2) 是否存在【有效 VP 审批】：发件邮箱匹配 people_roles，该人审批日 role=VP、审批日期在其有效期内、且不晚于 po_date，"
        "并在附件正文中明确批准当前 PO 的全部 service_items（部分批准/待确认/审批他单均无效）。\n"
        "两项必须同时满足才合规。只输出 COMPLIANT 或 NON_COMPLIANT。"
        % (
            rules_text,
            "\n".join("%s: %s" % (k, po.get(k, "")) for k in po),
            po.get("vendor_id", ""), po.get("vendor_name", ""),
            scope or "(未找到该供应商服务范围)",
            people_text,
            "\n\n".join(attach_texts) if attach_texts else "(无任何审批附件)",
        )
    )
    out = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=2000, enable_thinking=True)
    return skillio.clean_answer(out).upper()


def main() -> None:
    skillio.read_stdin_args()
    question = skillio.question_text()

    po_file = _find("purchase_order") or _find("purchase") or _find("_po")
    vendor_file = _find("vendor")
    people_file = _find("people") or _find("role")
    evidence_file = _find("approval_evidence") or _find("evidence")
    rules_file = _find("audit_rule") or _find("rule")

    pos = _read_csv_rows(_decode(po_file)) if po_file else []
    vendors_rows = _read_csv_rows(_decode(vendor_file)) if vendor_file else []
    evidence_rows = _read_csv_rows(_decode(evidence_file)) if evidence_file else []
    people_text = _decode(people_file)
    rules_text = _decode(rules_file) or question

    by_name = {p.name: p for p in skillio.list_files()}
    threshold = _threshold(rules_text + "\n" + question)

    def _amount(po):
        raw = re.sub(r"[^\d.]", "", str(po.get("amount_cny", "")))
        try:
            return float(raw) if raw else 0.0
        except Exception:
            return 0.0

    noncompliant = []
    for po in pos:
        pid = (po.get("po_id") or "").strip()
        if not pid:
            continue
        # Step 1+2: status + amount gate (deterministic).
        if not _status_ended(po.get("status", "")):
            continue
        if _amount(po) < threshold:
            continue
        # Step 3+: deep audit.
        attach = _attachments_for(pid, po.get("evidence_ids", ""), evidence_rows, by_name)
        if not attach:
            # No approval evidence at all -> cannot have valid VP approval.
            noncompliant.append(pid)
            continue
        scope = _vendor_scope(vendors_rows, po.get("vendor_id", ""), po.get("vendor_name", ""))
        try:
            verdict = _audit_one(po, scope, people_text, attach, rules_text)
        except Exception:
            verdict = ""
        if "NON_COMPLIANT" in verdict or verdict == "NON" or "不合规" in verdict:
            noncompliant.append(pid)

    noncompliant = sorted(set(noncompliant))
    skillio.emit(",".join(noncompliant))


if __name__ == "__main__":
    main()
