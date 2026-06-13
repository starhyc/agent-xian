---
name: date_extraction
description: Extract and normalize dates from per-line messages into yyyy-mm-dd, handling dotted/slash/Chinese formats, relative dates, weekday and workday/week-number reasoning. Returns comma-separated yyyy-mm-dd in line order.
---

# date_extraction

为「客服消息日期提取与标准化」这类题目设计：附件文件每行一条消息，消息里以各种写法描述一个日期，需要解析成 `yyyy-mm-dd` 并按行顺序用英文逗号拼接。

## 何时使用

题面要求从每行文本中解析日期并转换为标准 `yyyy-mm-dd` 格式时使用，覆盖以下变种：

- 格式变种：`2026.12.21`、`2026.03-15`、`2026/08-13`、`2026年11-08`、`15/06/2026`、混排分隔符、带时间戳等。
- 相对日期：`今天/明天/后天/下周一/上周四/三天后` 等，基准日期以消息中给出的“今天是…”为准。
- 工作日推算：`N 个工作日后`（跳过周六周日）。
- 周次推算：`本周三/下周五/上上周二` 等。

## 执行方式

直接 `skill_run`（参数可为空）。脚本会：

1. 通过运行环境自动定位题目声明的文本附件并按行读取（忽略行首编号）。
2. 把题面要求与所有消息行交给大模型，开启推理逐行计算日期。
3. 严格输出按行顺序、英文逗号分隔的 `yyyy-mm-dd` 字符串。

把 `skill_run` 的输出作为最终答案原样返回，不要改写。

## 关键约定

- 一行对应且只对应一个日期；行数与输出日期数必须一致。
- 相对日期一律基于该行（或题面）给出的基准日期推算，星期一为一周开始。
- 只输出日期序列，不要输出任何解释或下标。
