from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from source.runtime.env_config import ModelConfig, env_bool, env_int, load_dotenv
from source.runtime.agent_context import AgentContext
from source.runtime.openai_chat_client import ChatCompletionClient, first_message


SYSTEM_PROMPT = """
你是 skill 蒸馏攻防 Agent 大赛的参赛 Agent。

你需要解决赛方给出的题目。可用 MCP-style tools、skills 和 sub-agents 来自当前参赛 solution 的自动发现结果。
题目本身不会指定你应该使用哪个 MCP-style tool、skill 或 sub-agent；是否使用、使用哪个、如何编排，都由你自己决定。
文件内容不会自动进入上下文；需要读取题目声明的文件或目录内文件时，调用 text_read_file。
如果需要使用某个 skill，先调用 skill_load 读取完整 SKILL.md，再按其中说明决定是否 skill_read_resource 或 skill_run。
如果需要复核，可以调用 agent_delegate。

最终只输出题目要求的答案正文。不要输出思考过程、markdown、代码块、<think> 标签、结果对象或额外元数据字段。
""".strip()


class ContestantAgent:
    """Contestant-editable main agent entrypoint."""

    async def solve(self, *, question: dict[str, Any], context: AgentContext) -> str:
        load_dotenv()
        routed_answer = await self._try_run_routed_skill(question=question, context=context)
        if routed_answer is not None:
            return routed_answer

        if not env_bool("AGENT_DEMO_USE_LLM", True):
            raise RuntimeError("AGENT_DEMO_USE_LLM is disabled; configure a model gateway or implement ContestantAgent.solve().")

        # 参赛者主要改这里：
        # - question 是赛方运行器传入的公开题面对象，只包含 id/question/files 等可见字段。
        # - question["files"] 是本题允许读取的文件或目录列表，文件内容不会自动进入上下文。
        # - context 提供当前 solution 自动发现到的 MCP tools、skills、sub-agents 以及 call_tool(...) 调用入口。
        # - available_tools / available_skills / available_sub_agents 会一起传给模型，供主 Agent 自己决定是否调用。
        user_prompt = json.dumps(
            {
                "question": question,
                "files": question.get("files") or [],
                "available_tools": context.available_tools,
                "available_skills": context.available_skills,
                "available_sub_agents": context.available_agents,
                "tool_usage": "Call tools only when useful. Use text_read_file to read declared files; use skill_load before skill_run; use agent_delegate for sub-agents.",
                "final_output": "Return only the final answer text.",
            },
            ensure_ascii=False,
            indent=2,
        )

        return await self._run_model_loop(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            context=context,
        )

    async def _try_run_routed_skill(self, *, question: dict[str, Any], context: AgentContext) -> str | None:
        route = self._skill_route(question)
        if route is None or "skill_run" not in context.available_tools:
            return None

        skill_name, arguments = await self._build_skill_arguments(route, question, context)
        if not arguments:
            return None

        try:
            result = await context.call_tool("skill_run", {"name": skill_name, "arguments": arguments})
        except Exception:
            return None
        return self._extract_skill_answer(result, route)

    def _skill_route(self, question: dict[str, Any]) -> str | None:
        qid = str(question.get("id") or "").strip()
        if qid in {
            "1_1", "1_2", "1_3", "1_4", "2_1", "2_2", "2_3", "3_1", "3_2", "3_3",
        }:
            return qid

        text = str(question.get("question") or "")
        title_routes = [
            ("客服消息日期", "1_1"),
            ("华为编程规范", "1_2"),
            ("系统问题定位", "1_3"),
            ("接口测试结果", "1_4"),
            ("采购数据清洗", "2_1"),
            ("敏感信息扫描", "2_2"),
            ("Java个人所得税", "2_3"),
            ("提示词学习", "3_1"),
            ("采购PO合规审计", "3_2"),
            ("IDE 插件 FSE", "3_3"),
        ]
        for marker, route in title_routes:
            if marker in text:
                return route
        return None

    async def _build_skill_arguments(
        self,
        route: str,
        question: dict[str, Any],
        context: AgentContext,
    ) -> tuple[str, dict[str, Any]]:
        files = [str(path) for path in question.get("files") or []]
        first_path = self._first_declared_path(files, context.question_dir)
        question_text = str(question.get("question") or "")

        if route == "1_1":
            text = await self._read_declared_text(files[0], context) if files else ""
            args: dict[str, Any] = {"text": text}
            base_date = self._extract_base_date(question_text + "\n" + text)
            if base_date:
                args["base_date"] = base_date
            return "date_parser_skill", args
        if route == "1_2":
            return "huawei_coding_standards_skill", {
                "docs_directory": str(first_path),
                "question": question_text,
            }
        if route == "1_3":
            return "system_diagnosis_skill", {"base_dir": str(first_path.parent if first_path.is_file() else first_path)}
        if route == "1_4":
            return "api_tester_skill", {
                "docs_directory": str(first_path.parent if first_path.is_file() else first_path),
                "package_id": self._package_id(question),
            }
        if route == "2_1":
            return "purchase_data_cleaning_skill", {"data_dir": str(first_path)}
        if route == "2_2":
            return "sensitive_data_scanner_skill", {"scan_path": str(first_path)}
        if route == "2_3":
            return "java_tax_calculator_skill", {"java_file": str(first_path), "question": question_text}
        if route == "3_1":
            return "prompt_learning_skill", {"base_dir": str(first_path.parent), "question": question_text}
        if route == "3_2":
            return "po_compliance_audit_skill", {"data_dir": str(first_path)}
        if route == "3_3":
            return "fse_qa_skill", {"data_dir": str(first_path.parent if first_path.is_file() else first_path)}
        return "", {}

    def _first_declared_path(self, files: list[str], question_dir: Path) -> Path:
        if not files:
            return question_dir
        return (question_dir / files[0]).resolve()

    async def _read_declared_text(self, file_path: str, context: AgentContext) -> str:
        result = await context.call_tool("text_read_file", {"path": file_path, "max_chars": 2_000_000})
        return str(result)

    def _extract_base_date(self, text: str) -> str | None:
        match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
        if not match:
            return None
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    def _package_id(self, question: dict[str, Any]) -> str:
        qid = str(question.get("id") or "question")
        return f"agent-xian-{qid}"

    def _extract_skill_answer(self, result: Any, route: str) -> str:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return self._clean_final_answer(str(text))

        if isinstance(data, dict):
            answer = data.get("answer")
            if answer is not None:
                return str(answer)
            if route == "1_1" and isinstance(data.get("dates"), list):
                return ",".join(str(item) for item in data["dates"])
            if route == "1_4" and isinstance(data.get("failed_cases"), list):
                return ",".join(str(item) for item in data["failed_cases"])
            if route == "2_3" and isinstance(data.get("results"), list):
                version = data.get("java_version", "")
                return ",".join([str(version)] + [str(item) for item in data["results"]]).strip(",")
        return self._clean_final_answer(str(text))

    async def _run_model_loop(self, *, system_prompt: str, user_prompt: str, context: AgentContext) -> str:
        if not env_bool("AGENT_DEMO_NATIVE_TOOLS", True):
            return await self._run_json_tool_loop(system_prompt=system_prompt, user_prompt=user_prompt, context=context)

        try:
            return await self._run_native_tool_loop(system_prompt=system_prompt, user_prompt=user_prompt, context=context)
        except Exception:
            if env_bool("AGENT_DEMO_JSON_TOOL_FALLBACK", True):
                try:
                    return await self._run_json_tool_loop(system_prompt=system_prompt, user_prompt=user_prompt, context=context)
                except Exception:
                    pass
            raise

    async def _run_native_tool_loop(self, *, system_prompt: str, user_prompt: str, context: AgentContext) -> str:
        config = ModelConfig.from_env()
        client = ChatCompletionClient(config)
        tools = await context.mcp.list_openai_tools(
            allowed_tools=context.allowed_tools,
            allowed_agents=context.allowed_agents,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        max_iter = env_int("AGENT_DEMO_MAX_ITER", 6)
        for step in range(1, max_iter + 1):
            completion = await client.create(messages=messages, tools=tools, tool_choice="auto")
            message = first_message(completion)
            tool_calls = self._tool_calls_from_message(message)
            content = str(message.get("content") or "")

            messages.append(self._assistant_message_for_history(message))
            if not tool_calls:
                if content.strip():
                    return self._clean_final_answer(content)
                messages.append({"role": "user", "content": "请输出最终答案文本。"})
                continue

            for tool_call in tool_calls:
                tool_name = self._tool_call_name(tool_call)
                args_text = self._tool_call_arguments(tool_call)
                try:
                    tool_args = json.loads(args_text)
                except json.JSONDecodeError:
                    tool_args = {}
                tool_result = await self._call_tool_as_text(context, tool_name, tool_args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "name": tool_name,
                        "content": tool_result[:12000],
                    }
                )

        messages.append({"role": "user", "content": "请停止调用工具，直接输出最终答案文本。"})
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        return self._clean_final_answer(str(first_message(completion).get("content") or ""))

    async def _run_json_tool_loop(self, *, system_prompt: str, user_prompt: str, context: AgentContext) -> str:
        """Prompt-level JSON tool loop for gateways that reject native tools."""

        config = ModelConfig.from_env()
        client = ChatCompletionClient(config)
        tools = await context.mcp.list_openai_tools(
            allowed_tools=context.allowed_tools,
            allowed_agents=context.allowed_agents,
        )
        tool_specs = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "parameters": tool["function"].get("parameters", {}),
            }
            for tool in tools
        ]
        json_tool_prompt = (
            system_prompt
            + "\n\n当前模型网关可能不支持原生 tools 字段。"
            + "\n需要工具时，只输出 JSON，且第一个字符必须是 {，不要输出 markdown 代码块或思考过程："
            + '{"tool_calls":[{"name":"工具名","arguments":{}}]}'
            + "\n任务完成时，直接输出最终答案文本；不要包成结果对象。"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": json_tool_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt": user_prompt,
                        "available_tools": tool_specs,
                        "instruction": "如果需要工具，只输出 tool_calls JSON；如果不需要工具，直接输出最终答案文本。",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]

        max_iter = env_int("AGENT_DEMO_MAX_ITER", 6)
        for step in range(1, max_iter + 1):
            completion = await client.create(messages=messages, tools=[], tool_choice="none")
            content = str(first_message(completion).get("content") or "").strip()

            parsed = self._parse_json_object(content)
            tool_calls = self._json_prompt_tool_calls(parsed) if parsed else None
            if not tool_calls:
                if content:
                    return self._clean_final_answer(content)
                messages.append({"role": "user", "content": "请输出最终答案文本，或输出 tool_calls JSON。"})
                continue

            tool_results = []
            for call_index, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                tool_name = str(tool_call.get("name") or tool_call.get("tool") or "")
                tool_args = tool_call.get("arguments")
                if tool_args is None:
                    tool_args = {
                        key: value
                        for key, value in tool_call.items()
                        if key not in {"name", "tool"}
                    }
                if not isinstance(tool_args, dict):
                    tool_args = {}
                tool_results.append(
                    {
                        "index": call_index,
                        "name": tool_name,
                        "result": await self._call_tool_as_text(context, tool_name, tool_args),
                    }
                )

            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "tool_results": tool_results,
                            "instruction": "根据工具结果继续。还需要工具则输出 tool_calls JSON；完成则直接输出最终答案文本。",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )[:16000],
                }
            )

        messages.append({"role": "user", "content": "请停止请求工具，直接输出最终答案文本。"})
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        return self._clean_final_answer(str(first_message(completion).get("content") or ""))

    async def _call_tool_as_text(self, context: AgentContext, tool_name: str, tool_args: dict[str, Any]) -> str:
        try:
            tool_result = await context.call_tool(tool_name, tool_args)
        except Exception as exc:
            tool_result = f"工具调用失败：{exc}"
        if isinstance(tool_result, str):
            return tool_result
        return json.dumps(tool_result, ensure_ascii=False)

    def _assistant_message_for_history(self, message: dict[str, Any]) -> dict[str, Any]:
        history_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content") or "",
        }
        tool_calls = self._tool_calls_from_message(message)
        if tool_calls:
            history_message["tool_calls"] = tool_calls
        return history_message

    def _tool_calls_from_message(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            normalized = []
            for tool_call in tool_calls:
                if isinstance(tool_call.get("function"), dict):
                    normalized.append(tool_call)
                else:
                    normalized.append(
                        {
                            "id": tool_call.get("id", ""),
                            "type": tool_call.get("type", "function"),
                            "function": {
                                "name": tool_call.get("name", ""),
                                "arguments": tool_call.get("arguments") or "{}",
                            },
                        }
                    )
            return normalized
        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            return [
                {
                    "id": "legacy_function_call",
                    "type": "function",
                    "function": {
                        "name": function_call.get("name", ""),
                        "arguments": function_call.get("arguments") or "{}",
                    },
                }
            ]
        return []

    def _tool_call_name(self, tool_call: dict[str, Any]) -> str:
        if isinstance(tool_call.get("function"), dict):
            return str(tool_call["function"].get("name") or "")
        return str(tool_call.get("name") or "")

    def _tool_call_arguments(self, tool_call: dict[str, Any]) -> str:
        if isinstance(tool_call.get("function"), dict):
            return str(tool_call["function"].get("arguments") or "{}")
        return str(tool_call.get("arguments") or "{}")

    def _parse_json_object(self, content: str) -> dict[str, Any] | None:
        text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        candidates = [text]
        candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json|tool_calls)?\s*(.*?)```", text, flags=re.DOTALL))
        object_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if object_match:
            candidates.append(object_match.group(0))
        array_match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if array_match:
            candidates.append(array_match.group(0))

        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {"tool_calls": data}
        return None

    def _clean_final_answer(self, content: str) -> str:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return cleaned or content.strip()

    def _json_prompt_tool_calls(self, parsed: dict[str, Any]) -> list[dict[str, Any]] | None:
        tool_calls = parsed.get("tool_calls")
        if isinstance(tool_calls, list):
            return tool_calls
        if parsed.get("tool") or (parsed.get("name") and "arguments" in parsed):
            return [parsed]
        return None
