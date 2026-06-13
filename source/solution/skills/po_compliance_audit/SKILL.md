---
name: po_compliance_audit
description: Audit purchase orders — status+amount filter, then require vendor service-scope coverage AND a valid VP approval that covers all service_items of the exact PO. Returns non-compliant po_ids ascending, comma-separated.
---

# po_compliance_audit

为「采购 PO 合规审计」这类题目设计：依据 vendors、people_roles、approval_evidence 及附件正文和 audit_rules，判断哪些 PO 不合规。

## 何时使用

题面要求审计 PO 合规性、判定不合规 po_id 列表时使用。

## 审计步骤（严格按序）

1. 状态筛选：只审计语义上已结束/已完成/已支付/已验收/已关闭/已结案的 PO（中英文均可）；草稿/评审中/审批中/待补件/取消/作废等不进入。
2. 金额筛选：状态有效且 `amount_cny >= 阈值`（默认 50000，以题面为准）才进入深审。
3. 深审需同时满足，否则不合规：
   - 供应商服务范围：PO 的 service_items 必须全部落在该 vendor 的 service_scope 内。
   - VP 审批有效：审批附件明确覆盖当前 PO 且批准其全部 service_items；发件人邮箱匹配 people_roles，且该人在审批日期 role 为 VP、日期在其 VP 有效期内、且不晚于 po_date。Director/Manager/VP Assistant/过期 VP 都无效；“考虑一下/先评估/待补材料/只批一项/另行确认”等不算完整审批；审批其他 PO 的邮件不算。
4. 附件日期可能是多种自然语言格式，按语义理解。

## 返回格式

不合规 po_id 升序、英文逗号分隔；没有则输出空字符串。把 `skill_run` 输出作为最终答案原样返回，不要解释。

## 执行方式

直接 `skill_run`（参数可为空）。脚本读取全部 CSV、审计规则与附件正文，交由模型按上述步骤判定并输出。
