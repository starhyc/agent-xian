from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio
from source.solution.lib.llm import ask_with_images


# Deterministic-first procurement cleansing. The bulk of PO resolution
# (system fields valid, or evidence that is plain text) is done in code with
# zero model calls. Only POs whose decisive amount/currency/vendor evidence is
# an *image* invoice get OCR'd — chat screenshots / price comparisons are weak
# evidence per the rules and never decisive, so they are skipped. The final
# query aggregation is pure code, and the output always has one integer per
# query (no empty result).

OCR_PROMPT = (
    "这是一张采购发票图片。请原样抽取并只输出这些字段（每行一项，没有则留空）："
    "关联PO；发票状态；销售方法定名称；销售方税号(统一社会信用代码)；价税合计金额；币种。不要解释。"
)
IMG = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _read(p):
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return ""


def _rows(p):
    return list(csv.DictReader(io.StringIO(_read(p)))) if p else []


def _find(sub):
    for p in skillio.list_files([".csv"]):
        if sub in p.name.lower():
            return p
    return None


def _parse_amount(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("￥", "").replace("¥", "").replace("元", "").replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK]\b", s)
    if m:
        return int(round(float(m.group(1)) * 1000))
    m = re.search(r"\d+(?:\.\d+)?", s)
    return int(round(float(m.group(0)))) if m else None


def _norm_cur(raw):
    s = (raw or "").strip().upper()
    if not s:
        return None
    if "CNY" in s or "RMB" in s or "人民币" in s or "￥" in s or "¥" in s:
        return "CNY"
    if "USD" in s or "美元" in s or "$" in s:
        return "USD"
    if "EUR" in s or "欧元" in s:
        return "EUR"
    return s


class Vendors:
    def __init__(self, rows):
        self.by_id = {}
        self.by_tax = {}
        self.by_legal = {}
        self.by_brand = {}
        self.rows = rows
        for r in rows:
            vid = (r.get("vendor_id") or "").strip()
            self.by_id[vid] = r
            tax = (r.get("tax_id") or "").strip()
            if tax:
                self.by_tax[tax] = vid
            self.by_legal[(r.get("legal_name") or "").strip()] = vid
            b = (r.get("brand_name") or "").strip().lower()
            if b:
                self.by_brand[b] = vid

    def resolve_raw(self, raw):
        """Unique vendor_id from a raw name/brand, else None if 0 or >1 match."""
        raw = (raw or "").strip()
        if not raw:
            return None
        if raw in self.by_legal:
            return self.by_legal[raw]
        if raw.lower() in self.by_brand:
            return self.by_brand[raw.lower()]
        hits = {vid for name, vid in self.by_legal.items() if raw and raw in name}
        hits |= {vid for b, vid in self.by_brand.items() if raw.lower() and raw.lower() in b}
        return next(iter(hits)) if len(hits) == 1 else None


def _parse_invoice_text(text):
    """Extract (related_po, status_void, legal_name, tax_id, amount, currency)."""
    def grab(*pats):
        for pat in pats:
            m = re.search(pat, text)
            if m:
                return m.group(1).strip()
        return None
    po = grab(r"关联\s*PO[:：]\s*(\S+)", r"PO[:：]?\s*(PO-\d{4}-\d+)")
    status = grab(r"发票状态[:：]\s*(\S+)")
    void = bool(status and re.search(r"作废|无效|旧版|错误|red|red-?ink|冲红", status))
    legal = grab(r"销售方[:：]\s*([^\n，,]+)", r"开票方[:：]\s*([^\n，,]+)")
    tax = grab(r"(?:销售方)?税号[^:：]*[:：]\s*([0-9A-Z]+)", r"统一社会信用代码[:：]\s*([0-9A-Z]+)")
    amt_raw = grab(r"价税合计[:：]?\s*([^\n]+)", r"金额[:：]\s*([^\n]+)")
    cur = None
    amount = None
    if amt_raw:
        cur = _norm_cur(amt_raw)
        amount = _parse_amount(amt_raw)
    if cur is None:
        cur = _norm_cur(grab(r"币种[:：]\s*(\S+)") or "")
    return {"po": po, "void": void, "legal": legal, "tax": tax, "amount": amount, "currency": cur}


def _contract_party(text):
    m = (re.search(r"相对方[:：]\s*([^\n，,；;]+)", text)
         or re.search(r"Registered Entity[:：]\s*([^\n，,；;]+)", text, re.I)
         or re.search(r"Counterparty[:：]\s*([^\n，,；;]+)", text, re.I))
    void = bool(re.search(r"作废|旧版|错误关联|错\s*PO|void|obsolete|wrong\s*PO", text, re.I))
    return (m.group(1).strip() if m else None), void


def _contract_scope(text):
    m = (re.search(r"服务范围[:：]\s*([^\n]+)", text)
         or re.search(r"Service Scope[:：]\s*([^\n]+)", text, re.I))
    return m.group(1).strip() if m else ""


def _category_keywords(tax_rows):
    """Per category, the keyword phrases drawn from its own taxonomy row."""
    kw = {}
    for r in tax_rows:
        code = (r.get("category_code") or "").strip()
        if not code:
            continue
        blob = " ".join((r.get("category_name") or "", r.get("definition") or ""))
        phrases = [p.strip() for p in re.split(r"[、，,；;。\s]+", blob) if len(p.strip()) >= 2]
        kw[code] = set(phrases)
    return kw


def _derive_category(text, kwmap):
    """Unique best-matching category_code for a contract service scope, else None."""
    if not text:
        return None
    scores = []
    for code, phrases in kwmap.items():
        s = sum(1 for p in phrases if p and p in text)
        scores.append((s, code))
    scores.sort(reverse=True)
    if scores and scores[0][0] >= 2 and (len(scores) < 2 or scores[0][0] > scores[1][0]):
        return scores[0][1]
    return None


def main() -> None:
    skillio.read_stdin_args()

    po_f = _find("purchase_order") or _find("purchase")
    ven_f = _find("vendor")
    tax_f = _find("category") or _find("taxonomy")
    man_f = _find("manifest") or _find("attachment")
    q_f = _find("quer")

    pos = _rows(po_f)
    vendors = Vendors(_rows(ven_f))
    tax_rows = _rows(tax_f)
    cats = {(r.get("category_code") or "").strip() for r in tax_rows}
    cat_kw = _category_keywords(tax_rows)
    queries = _rows(q_f)

    # attachment_manifest: po_id -> [(type, abs_path)]
    by_name = {p.name: p for p in skillio.list_files()}
    att_by_po = {}
    qdir = skillio.question_dir()
    for m in _rows(man_f):
        pid = (m.get("po_id") or "").strip()
        fp = (m.get("file_path") or "").strip()
        path = by_name.get(Path(fp).name) or (qdir / fp if qdir else None)
        att_by_po.setdefault(pid, []).append({"type": (m.get("attachment_type") or "").strip(), "path": path})

    def evidence_text(pid, want_types):
        """Concatenate decisive *text* attachments for a PO (invoice/contract)."""
        items = []
        for a in att_by_po.get(pid, []):
            if a["type"] not in want_types:
                continue
            p = a["path"]
            if p is None or not Path(p).exists():
                continue
            if Path(p).suffix.lower() == ".txt":
                items.append((a["type"], _read(Path(p))))
        return items

    def image_invoices(pid):
        out = []
        for a in att_by_po.get(pid, []):
            if a["type"] != "invoice":
                continue
            p = a["path"]
            if p is not None and Path(p).suffix.lower() in IMG:
                out.append(Path(p))
        return out

    def resolve(pid, po, ocr_text_for_pid):
        vendor = (po.get("vendor_id") or "").strip()
        if vendor not in vendors.by_id:
            vendor = None
        category = (po.get("category_code") or "").strip()
        if category not in cats:
            category = None
        amount = _parse_amount(po.get("amount_raw"))
        currency = _norm_cur(po.get("currency"))

        # ---- decisive evidence: text invoices (+ OCR'd image invoice text) ----
        inv_texts = [t for _, t in evidence_text(pid, {"invoice"})]
        if ocr_text_for_pid:
            inv_texts.append(ocr_text_for_pid)
        for t in inv_texts:
            inv = _parse_invoice_text(t)
            if inv["void"]:
                continue
            if inv["po"] and pid and inv["po"] != pid:
                continue  # wrong-PO invoice is not valid evidence
            # vendor: invoice legal entity / USCC overrides
            if inv["tax"] and inv["tax"] in vendors.by_tax:
                vendor = vendors.by_tax[inv["tax"]]
            elif inv["legal"] and inv["legal"] in vendors.by_legal:
                vendor = vendors.by_legal[inv["legal"]]
            # currency
            if inv["currency"]:
                if currency and currency != inv["currency"]:
                    return None  # conflict
                currency = inv["currency"]
            # amount: fill if missing; exclude if system amount > invoice
            if inv["amount"] is not None:
                if amount is None:
                    amount = inv["amount"]
                elif amount > inv["amount"]:
                    return None

        # ---- vendor via contract party / raw name (text contracts) ----
        contract_texts = [t for _, t in evidence_text(pid, {"contract_excerpt", "contract"})]
        if vendor is None:
            for t in contract_texts:
                party, cvoid = _contract_party(t)
                if cvoid:
                    continue
                if party and party in vendors.by_legal:
                    vendor = vendors.by_legal[party]
                    break
                if party:
                    rv = vendors.resolve_raw(party)
                    if rv:
                        vendor = rv
                        break
        if vendor is None:
            vendor = vendors.resolve_raw(po.get("vendor_name_raw"))

        # ---- category: trust valid system code unless a contract scope clearly
        # conflicts; derive from contract when missing/invalid. (chat/quote/
        # invoice item names cannot define category per the rules.) ----
        scope_blob = " ".join(_contract_scope(t) or t for t in contract_texts)
        derived = _derive_category(scope_blob, cat_kw)
        if category is None:
            category = derived
        elif derived and derived != category:
            category = derived  # contract service scope overrides a conflicting system code

        if vendor and category and amount is not None and currency == "CNY":
            return {"vendor_id": vendor, "category_code": category, "amount": amount}
        return None

    # Pass 1: deterministic (no model). Queue POs that still lack amount/currency
    # AND have an image invoice that could supply it.
    resolved = {}
    need_ocr = []
    for po in pos:
        pid = (po.get("po_id") or "").strip()
        if not pid:
            continue
        r = resolve(pid, po, None)
        if r is not None:
            resolved[pid] = r
            continue
        # only OCR when an image invoice might decide amount/currency/vendor
        amt = _parse_amount(po.get("amount_raw"))
        cur = _norm_cur(po.get("currency"))
        if image_invoices(pid) and (amt is None or cur is None or cur == "CNY"):
            need_ocr.append((pid, po))

    # Pass 2: OCR only the image-invoice POs (bounded), then re-resolve.
    for pid, po in need_ocr:
        ocr_text = ""
        for img in image_invoices(pid):
            try:
                ocr_text += "\n" + ask_with_images(OCR_PROMPT, [img], max_tokens=300)
            except Exception:
                pass
        r = resolve(pid, po, ocr_text)
        if r is not None:
            resolved[pid] = r

    # Aggregate by query (vendor_id, category_code), CNY, 2026 (all data is 2026).
    out = []
    for q in queries:
        qv = (q.get("vendor_id") or "").strip()
        qc = (q.get("category_code") or "").strip()
        total = 0
        for r in resolved.values():
            if r["vendor_id"] == qv and r["category_code"] == qc:
                total += r["amount"]
        out.append(str(total))

    skillio.emit(",".join(out))


if __name__ == "__main__":
    main()
