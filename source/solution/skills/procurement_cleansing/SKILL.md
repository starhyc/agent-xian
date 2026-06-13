---
name: procurement_cleansing
description: Cleanse raw purchase orders against vendor master data, category taxonomy and per-PO attachment evidence (text or image), then sum valid CNY amounts per query. Returns comma-separated integer totals in query order.
---

# procurement_cleansing

为「采购数据清洗与汇总」这类题目设计：对原始采购录入做内部清洗，再按 `queries.csv` 计算每个查询对应的 CNY 采购总额。

## 何时使用

题面要求结合 vendors / category_taxonomy / 原始 PO / 附件证据做清洗，并按 query 汇总 CNY 总额时使用。

## 清洗与统计口径（严格遵守）

- 只有能唯一确认供应商、品类、金额、币种的 PO 才计入；任一项无法唯一确认则不计入。
- 有效附件：关联当前 PO、未作废、非旧版、非错 PO；作废/旧版/错 PO 附件不能作证据。
- 供应商：vendor_id 缺失/无效/与有效发票合同主体冲突时，依据 vendors.csv 的法定名称、统一社会信用代码、英文品牌、有效附件重新唯一确认；否则不计入。
- 品类：最终须唯一对应 category_taxonomy 的 category_code；缺失/无效/与有效合同服务范围冲突时据有效合同重定；聊天/报价/比价/发票项目名只是用途线索，不能单独定类。
- 金额：系统金额清晰且未被有效附件否定→用系统金额；系统金额缺失→只可用匹配当前 PO 的有效发票补；系统金额 > 有效发票金额→不计入；合同/预算/报价/聊天估算都不能补或覆盖金额。
- 币种：只统计 CNY；系统或有效发票币种为 USD/EUR 等→不计入，不换算；币种缺失只能用有效发票确认。
- 按 queries.csv 的 query_id 升序输出整数总额（无逗号），无匹配输出 0；输出项数必须与 queries.csv 行数一致。

## 执行方式

直接 `skill_run`（参数可为空）。脚本会读取全部主数据 CSV、文本附件，并对图片附件做视觉抽取（金额/币种/法定名称/统一社会信用代码/服务范围），汇总成证据后交由模型按上述口径逐 query 计算，输出逗号分隔的整数串。把输出作为最终答案原样返回。
