from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio, gather
from source.solution.lib.llm import ask


SYSTEM = (
    "你是严谨的采购 PO 合规审计员。严格按题面与 audit_rules 的步骤："
    "先状态筛选，再金额阈值筛选，再对深审 PO 同时核对供应商服务范围覆盖与有效 VP 审批。"
    "VP 审批要按发件邮箱匹配 people_roles，确认审批日 role 为 VP、在有效期内、不晚于 po_date，"
    "并阅读审批附件正文确认明确批准当前 PO 的全部 service_items。只输出结果，不要解释。"
)


def main() -> None:
    skillio.read_stdin_args()
    text = gather.gather_text()
    question = skillio.question_text()
    prompt = (
        "题目与审计规则：\n%s\n\n"
        "全部数据（含 vendors、people_roles、purchase_orders、approval_evidence 与附件正文、audit_rules）：\n%s\n\n"
        "请逐个 PO 走完：状态筛选 → 金额阈值筛选 → 服务范围覆盖 + VP 审批有效性。"
        "把不合规的 po_id 收集起来，按 po_id 升序、用英文逗号连接输出；没有则输出空字符串。不要任何解释。"
        % (question, text)
    )
    answer = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=800, enable_thinking=True)
    skillio.emit(answer)


if __name__ == "__main__":
    main()
