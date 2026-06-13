# Agent 方案设计说明

## 总体思路

主 Agent 负责「路由」，能力沉淀在 Skill 中，确定性操作由 MCP 工具承担，判断交给大模型。

- **主 Agent**（`source/solution/contestant_agent.py`）：读题面 → 匹配并加载 Skill（`skill_load`）→ 执行（`skill_run`，参数通常为空）→ 原样返回 Skill 输出。无匹配 Skill 时退化为直接用 MCP 工具读文件并自行推理。
- **Skill**（`source/solution/skills/<name>/`）：每个题型一个技能包，`SKILL.md` 写方法论，`scripts/run.py` 为可执行体。脚本自动发现题目文件、做必要的确定性处理（解析/解压/正则/HTTP/SQLite/编译），把判断/抽取交给大模型，最终返回题目要求的答案串。
- **MCP 工具**（`source/solution/mcp/contestant_tools.py`）：`list_dir`、`read_text_file`、`extract_archive`、`http_request`、`sqlite_query`、`vision_query`、`run_command`，均按题目声明文件/临时工作区做路径校验。

## 关键机制：把题目上下文透传给 Skill

赛方运行器只为 `text_read_file` 解析路径。为让工具与 Skill 子进程也能访问题目文件：

1. `LocalMCPClient.call_tool` 调用前把 `runtime_context` 发布到进程内 `source/runtime/tool_context.py`。
2. `SkillRuntime.run_skill` 把 `QUESTION_DIR`、`QUESTION_TEXT`、`QUESTION_ID`、`ALLOWED_FILE_PATHS`、`WORKSPACE_ROOT`、`PYTHONPATH` 注入 Skill 子进程环境。
3. Skill 通过 `source/solution/lib/skillio.py` 读回这些信息，自动定位文件、读取题面文本。

这样 Skill 不依赖主 Agent 传参，对题目「变种」更鲁棒。

## 共享库（`source/solution/lib/`）

- `llm.py`：同步网关客户端（文本 + 多模态图片），自动重试、透传 `package_id`/`X-Package-Id`。
- `skillio.py`：文件发现、题面读取、答案清洗（去 `<think>`/代码块）。
- `gather.py`：汇总文本证据 + 图片视觉抽取。
- `archives.py`：递归解压 zip/tar（含嵌套）。

## 各题 Skill

| 题 | Skill | 确定性部分 | 模型部分 |
|----|-------|-----------|---------|
| 1_1 | date_extraction | 读行 | 逐行日期归一(含相对/工作日/周次推算) |
| 1_2 | coding_standard_qa | 读规范文档 | 按原文逐题作答 |
| 1_3 | defect_root_cause | 读 log/har/schema、解压截图 | 构建唯一闭环作业流 + 根因映射 |
| 1_4 | api_test_runner | 真实 HTTP 执行 + 确定性断言 | 把用例自然语言步骤规划成请求 |
| 2_1 | procurement_cleansing | 读 CSV/文本、图片 OCR | 清洗口径判断 + 按 query 汇总 |
| 2_2 | sensitive_info_scan | 递归解压 + 正则统计 | 图片文本转写 |
| 2_3 | java_fix_run | 解码自带税表确定性计算 + 取真实 java 版本 | （可选）修复并编译运行交叉校验 |
| 3_1 | image_prompt_classify | 训练集标签解析 | 学习判别提示词 + 逐图分类 |
| 3_2 | po_compliance_audit | 读 CSV/附件正文 | 状态/金额筛选 + 服务范围 + VP 审批判定 |
| 3_3 | fse_persona_qa | DB 候选 + Wiki FAQ 拉取、确定性 persona 与 action 映射 | 为每问选出唯一来源记录 |

3_3 的 `reply`/`service_action` 取自所选来源原文与 key 映射，模型只负责「选记录」，从而保证逐字一致。

## 运行

```bash
bash start.sh <questions_json> <results_json> <package_id>
```

`.env` 中 `MODEL_CHAT_COMPLETIONS_URL`/`MODEL_API_KEY`/`MODEL_NAME` 保持原样。每完成一题即刷新写入 `results.json`。
