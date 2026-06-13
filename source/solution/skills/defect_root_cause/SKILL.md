---
name: defect_root_cause
description: Locate a system defect by building one complete front-office workflow evidence chain across screenshots, frontend log, backend validation log, network HAR and form_schema rules, then map validation codes to root causes. Returns 'module,interface,rootCauseKeywords'.
---

# defect_root_cause

为「系统问题定位与根因判断」这类题目设计。需要把页面截图、前端日志、后端校验日志、网络抓包(HAR) 和 form_schema 规则抽取为证据池，找出唯一一条能跨文件闭环的完整前台作业流，并据此判定缺陷模块、异常接口和根因关键词。

## 何时使用

题面给出多份定位材料（log/har/json，可能含截图 zip），要求判断问题模块、接口与一个或多个根因时使用。

## 核心判定原则

1. 先把所有材料抽取为证据：页面记录、前端人工提交事件、网络请求、后端校验结果、最终可见失败呈现、根因规则。
2. 最终答案必须来自**同一条完整前台作业流**：能连续关联页面→前端提交→网络请求→后端校验→可见失败。
3. 排除干扰：只局部字段关联、相似截图、相邻重试、诊断回放、预校验、静态资源异常、后台任务候选都不能作为依据。不要跨 workflowId/requestId/actionId/traceId/validationRef 合并。
4. 按 `form_schema.json` 决定校验码如何过滤与映射：
   - 仅 `validationCodeMap`：锁定链路后，把该链路下的 validationCode 映射成根因关键词。
   - 含 `effectiveValidationRule`：只有满足规则字段要求的 validationCode 有效，排除同链路下的预校验/诊断/噪声码。
   - 含 `rootCauseRules`：按 module/validationCode/fieldPath/schemaVersion 等条件匹配，命中的 rootCause 才输出。
5. 同一完整作业流下，凡有效 validationCode 能映射到根因关键词的，必须全部输出。

## 返回格式

`缺陷模块,异常接口,根因关键词`，多个根因关键词用中文顿号 `、` 连接；不要输出解释。

## 执行方式

直接 `skill_run`（参数可为空）。脚本自动读取所有文本材料、解析 HAR、按 form_schema 规则做证据关联与映射（截图若存在会做视觉抽取），交由模型推理出唯一闭环链路并输出结果。把输出作为最终答案原样返回。
