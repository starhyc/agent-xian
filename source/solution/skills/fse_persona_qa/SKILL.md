---
name: fse_persona_qa
description: Answer DevPilot IDE-plugin FSE digital-human support questions by selecting the matching source (chat_history.db keyed messages or local Wiki FAQs), taking reply VERBATIM and resolving service_action via the key map. Returns a JSON array text.
---

# fse_persona_qa

为「IDE 插件 FSE 数字人问答」这类题目设计：依据 persona、历史处理记录(SQLite)、source_access 和本地 Wiki 服务，按固定格式回答问题。

## 何时使用

题面提供 persona.md、chat_history.db、source_access.json、dialog_tests 等，要求按 `题目ID=>persona_phrase|||reply|||service_action` 输出 JSON 数组时使用。

## 关键规则（务必遵守）

- **reply 必须取自来源原文**：Wiki `get_page` 返回的 FAQ `a` 字段，或 chat_history.db 的 `messages.content` 原文，逐字一致，不得改写/翻译/增删/总结。
- **service_action 来自所选来源的 service_action_key**，再经 `source_access.service_action_key_map` 映射为 `service_action_options` 中的某一项原文。不得按题目 ID/字段名/样例顺序猜测。
- **persona_phrase**：题目给出 `user.name` 用「您好{name}总」；为空或缺失用「您好老师」。
- 资料存在 DB/Wiki 双源或新旧时间线冲突时，按题目上下文选择正确且最新的权威来源；Wiki 的 search/list 仅是候选，最终以 get_page 的 FAQ 详情为准。
- 每个元素仅含一个 `=>` 和两个 `|||`；persona/reply/service_action 内部不得含 `=>`、`|||` 或换行。

## 执行方式

直接 `skill_run`（参数可为空）。脚本会：

1. 读取 source_access、persona、dialog 列表；从 chat_history.db 取带 action key 的权威消息；向本地 Wiki 服务拉取各页 FAQ。
2. 对每个问题用模型从候选池中**选出唯一来源记录**（只选 id，不生成正文）。
3. 用所选来源的原文 reply、映射后的 service_action 和确定性 persona_phrase 组装结果，按问题顺序输出 JSON 数组文本。

把 `skill_run` 输出作为最终答案原样返回，不要改写。
