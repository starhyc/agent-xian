from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio, gather
from source.solution.lib.llm import ask


SYSTEM = (
    "你是严谨的采购数据清洗与汇总引擎。严格按题面口径判断每个 PO 能否唯一确认"
    "供应商、品类、金额、币种，只统计 CNY，按 query 汇总有效 PO 金额。"
    "只输出最终结果，不要解释。"
)

OCR_PROMPT = (
    "这是采购附件图片(可能是发票/合同节选/聊天截图/报价单)。请抽取关键信息："
    "发票金额与币种、开票方法定名称与统一社会信用代码、合同相对方法定名称与服务范围、"
    "是否作废/旧版、关联的 PO 号或附件号、聊天中的采购用途线索。只输出要点。"
)


def main() -> None:
    skillio.read_stdin_args()
    text = gather.gather_text()
    images = gather.ocr_images(OCR_PROMPT)
    question = skillio.question_text()

    evidence = text
    if images:
        evidence += "\n\n===== 图片附件视觉抽取 =====\n" + images

    prompt = (
        "题目与清洗/汇总口径：\n%s\n\n"
        "全部主数据与证据如下：\n%s\n\n"
        "请：1) 用 attachment_manifest 把附件映射到 PO，排除作废/旧版/错PO附件；"
        "2) 对每个 PO 唯一确认供应商、品类(对应 category_taxonomy 的 category_code)、金额、币种，"
        "任一不能唯一确认则该 PO 不计入；只统计 CNY；"
        "3) 按 queries.csv 的 query_id 升序，对匹配 (vendor_id, category_code, year, currency) 的有效 PO 金额求和。"
        "最终只输出与 queries.csv 行数一致、按 query_id 升序、英文逗号分隔的整数总额，无匹配输出 0，不要任何解释。"
        % (question, evidence)
    )
    answer = ask(prompt, system=SYSTEM, temperature=0.0, max_tokens=1200, enable_thinking=True)
    skillio.emit(answer)


if __name__ == "__main__":
    main()
