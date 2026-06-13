# 采购PO合规审计

## 能力描述

审计采购订单的合规性，识别不合规PO。需要进行状态筛选、金额筛选、供应商服务范围校验和VP审批有效性校验。

## 输入

```json
{
  "data_dir": "./采购PO合规审计/"
}
```

- `data_dir`: 数据文件目录，包含：
  - `purchase_orders_raw.csv`: 采购订单原始数据
  - `vendors.csv`: 供应商主数据（含服务范围）
  - `people_roles.csv`: 人员角色数据（含VP有效期）
  - `approval_evidence.csv`: 审批证据索引
  - `audit_rules.md`: 审计规则说明
  - `attachments/`: 审批附件目录

## 处理逻辑

### 第一步：状态筛选
只审计状态语义明确表示已结束、已完成、已支付、已验收、已关闭或已结案的PO。

有效状态关键词（中英文）：
- 已完成、completed、closed、paid、accepted、已支付、已验收、已关闭、已结案

无效状态（排除）：
- 草稿、draft、评审中、under_review、审批中、待补件、cancelled、取消、作废

### 第二步：金额筛选
只审计 amount_cny >= 50000 CNY 的PO。

### 第三步：供应商服务范围校验
深审PO的service_items必须全部落在vendors.csv中该供应商的service_scope内。

解析规则：
1. 按顿号"、"或逗号分隔service_items
2. 逐项检查是否在vendor的service_scope中
3. 任意一项不在范围内则不合规

### 第四步：VP审批校验
有效审批必须同时满足：
1. 审批证据明确覆盖当前PO（PO号在邮件中出现）
2. 审批证据明确批准当前PO的全部service_items
3. 发件人邮箱能匹配people_roles.csv
4. 该邮箱对应人员在审批日期的role为VP
5. 审批日期处于该VP身份有效期内
6. 审批日期不晚于PO的po_date

无效审批特征（排除）：
- "考虑一下"、"先评估"、"待补材料"、"只批准其中一项"、"后续另行确认"
- Director、Manager、VP Assistant或过期VP的审批
- 审批其他PO的邮件

### 日期解析
附件中日期可能有多种格式：
- "2026年4月14日"
- "Apr 15, 2026"
- "2026-04-25"

## 输出

```json
{
  "answer": "PO-2026-0003,PO-2026-0013,...",
  "total_pos": 50,
  "status_filtered": 35,
  "amount_filtered": 20,
  "deep_audited": 15,
  "non_compliant": 9
}
```

- `answer`: 不合规PO的po_id，按升序排列，逗号分隔
- `total_pos`: 总PO数量
- `status_filtered`: 状态筛选后剩余PO数量
- `amount_filtered`: 金额筛选后剩余PO数量
- `deep_audited`: 深审PO数量
- `non_compliant`: 不合规PO数量

## 变种覆盖

1. 状态表达变种：中英文混合
2. 金额阈值变种：阈值可能调整
3. 审批内容变种：多轮转发/追问、附件数量变化
4. 日期格式变种：多种常见日期格式
5. 服务范围变种：供应商服务范围可能调整