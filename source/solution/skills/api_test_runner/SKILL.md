---
name: api_test_runner
description: Execute API test cases against a local HTTP service in order via real calls and report which case IDs fail. Handles token fetch/reuse, X-Package-Id, multi-step cases and field/value assertions.
---

# api_test_runner

为「接口测试结果识别」这类题目设计：根据 `api_doc.md`、`test_cases.json`、`auth_config.json`，对本地接口服务发起真实调用，按用例顺序执行并判断哪些用例未通过。

## 何时使用

题面提供接口文档 + 测试用例 + 鉴权配置，并要求通过真实接口调用判断失败用例时使用。

## 关键约定

- 用例严格按 `test_cases.json` 出现顺序执行；部分用例需要连续调用多个接口，只校验描述中指定的那次响应。
- 所有请求都要携带 `X-Package-Id` 请求头（值来自运行环境，可能为空，但请求头必须存在）。
- 写接口需要先 `POST /api/auth/token` 取得 token，并以 `Authorization: Bearer <accessToken>` 调用；同一 token 仅能成功调用一次写接口，再次复用会 401（除非用例本身就是校验 401）。
- 整轮使用同一个 packageId，服务按 packageId 保留删除/状态/标签/备注/归档等运行态数据。
- 断言以 `expectedStatus` / `expectedFields` / `expectedValues` 为准。

## 执行方式

直接 `skill_run`（参数可为空）。脚本会：

1. 读取三份输入文件并先做一次 `/api/debug/reset`（按 packageId 复位运行态，保证可复现）。
2. 对每个用例，用大模型把自然语言步骤规划成有序的 HTTP 请求列表（含取 token、写接口、最后校验哪一次响应）。
3. 真实执行请求（自动注入 X-Package-Id、回填 accessToken），并按 `assert` 做确定性断言。
4. 输出按用例顺序、英文逗号分隔的失败用例 ID。

把输出作为最终答案原样返回，不要改写。
