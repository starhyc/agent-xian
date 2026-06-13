from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# 复用大模型客户端
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from source.runtime.openai_chat_client import ChatCompletionClient, first_message
from source.runtime.env_config import ModelConfig, load_dotenv


def log(msg: str) -> None:
    print(f"[java_tax] {msg}", file=sys.stderr)


def get_java_version() -> str:
    """获取 Java 版本输出片段"""
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stderr or result.stdout).strip()
        match = re.search(r'(?:openjdk|java)\s+version\s+"[^"]+"', output, flags=re.IGNORECASE)
        if match:
            return match.group(0)
        match = re.search(r'(\d+\.\d+\.\d+)', output)
        if match:
            return f'openjdk version "{match.group(1)}"'
        return "unknown"
    except Exception:
        return "unknown"


def find_class_name(source_code: str) -> str | None:
    """Find the public class name in Java source."""
    match = re.search(r"\bpublic\s+(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)", source_code)
    if match:
        return match.group(1)
    match = re.search(r"\bclass\s+([A-Za-z_]\w*)", source_code)
    return match.group(1) if match else None


def compile_java(java_file: Path, output_dir: Path) -> tuple[bool, str, str]:
    """Compile a Java source file."""
    try:
        compile_cmd = ["javac", "-d", str(output_dir), str(java_file)]
        result = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Compilation timeout"
    except Exception as exc:
        return False, "", str(exc)


def run_java(class_name: str, class_dir: Path, input_data: str) -> tuple[int, str, str]:
    """Run a compiled Java class with input."""
    try:
        run_cmd = ["java", "-cp", str(class_dir), class_name]
        result = subprocess.run(
            run_cmd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode, result.stdout.strip(), result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Execution timeout"
    except Exception as exc:
        return -1, "", str(exc)


def fix_java_source(source_code: str, compile_error: str) -> str:
    """使用大模型修复 Java 源码中的错误"""
    import asyncio

    system_prompt = """你是一个 Java 编程专家。

给定一个有错误的 Java 源码和编译错误信息，你需要修复这些错误。

规则：
1. 只修复编译错误，不要改变程序的功能逻辑
2. 保持原有的注释和代码风格
3. 修复后的代码必须能够编译通过
4. 如果有逻辑错误需要修复，根据注释中的计算规则修复

返回格式：
```java
[修复后的完整源码]
```

只输出修复后的代码，不要输出其他内容。"""

    user_prompt = f"""Java 源码：
{source_code}

编译错误：
{compile_error}

请修复错误并返回完整的修复后代码。"""

    async def _call_llm():
        load_dotenv()
        config = ModelConfig.from_env()
        client = ChatCompletionClient(config)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        completion = await client.create(messages=messages, tools=[], tool_choice="none")
        return first_message(completion).get("content", "")

    try:
        content = asyncio.run(_call_llm())
        log("LLM returned fixed code")
        
        # 提取代码块中的内容
        code_match = re.search(r"```java\n?(.*?)```", content, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        
        # 如果没有代码块，直接返回内容
        return content.strip()
    except Exception as exc:
        log(f"LLM call failed: {exc}")
        return source_code  # 返回原始代码


def parse_number_list(text: str) -> list[str]:
    return re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", text)


def parse_hidden_inputs(payload: dict[str, Any]) -> list[str]:
    values = payload.get("hidden_inputs")
    if isinstance(values, list) and values:
        return [str(item) for item in values]

    question = str(payload.get("question") or "")
    hidden_match = re.search(r"【隐藏用例】.*?(?:\n\n|【返回格式】|$)", question, flags=re.DOTALL)
    if hidden_match:
        numbers = [
            line.strip()
            for line in hidden_match.group(0).splitlines()
            if re.fullmatch(r"-?\d+(?:\.\d+)?", line.strip())
        ]
        if numbers:
            return numbers

    return ["5000", "12000", "25000", "35000", "55000", "60000", "80000", "90000", "150000", "500000"]


def parse_examples(payload: dict[str, Any]) -> list[tuple[str, str]]:
    explicit = payload.get("examples")
    if isinstance(explicit, list):
        pairs = []
        for item in explicit:
            if isinstance(item, dict) and "input" in item and "output" in item:
                pairs.append((str(item["input"]), str(item["output"])))
        if pairs:
            return pairs

    question = str(payload.get("question") or "")
    sample_match = re.search(r"【示例输入输出】(.*?)(?:【隐藏用例】|【返回格式】|$)", question, flags=re.DOTALL)
    if not sample_match:
        return []
    pairs = []
    for line in sample_match.group(1).splitlines():
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:->|=>|：|:)\s*(-?\d+(?:\.\d+)?)", line)
        if match:
            pairs.append((match.group(1), f"{float(match.group(2)):.2f}"))
    return pairs


def extract_numeric_output(stdout: str) -> str | None:
    numbers = re.findall(r"-?\d+(?:\.\d+)?", stdout)
    if not numbers:
        return None
    return f"{float(numbers[-1]):.2f}"


def write_source_for_compile(source_code: str, class_name: str, temp_dir: Path) -> Path:
    source_path = temp_dir / f"{class_name}.java"
    source_path.write_text(source_code, encoding="utf-8")
    return source_path


def compile_source_code(source_code: str, class_name: str, output_dir: Path, temp_dir: Path) -> tuple[bool, str, str, Path]:
    source_path = write_source_for_compile(source_code, class_name, temp_dir)
    ok, stdout, stderr = compile_java(source_path, output_dir)
    return ok, stdout, stderr, source_path


def validate_examples(class_name: str, output_dir: Path, examples: list[tuple[str, str]]) -> tuple[bool, str]:
    failures = []
    for input_value, expected in examples:
        return_code, stdout, stderr = run_java(class_name, output_dir, input_value)
        actual = extract_numeric_output(stdout) if return_code == 0 else None
        if actual != expected:
            failures.append(f"{input_value}: expected {expected}, got {actual or stderr or stdout}")
    return not failures, "\n".join(failures)


def main() -> None:
    """Main entry point for the skill."""
    log("=== Skill started ===")
    
    raw = sys.stdin.read().strip() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    java_file = payload.get("java_file", "")
    log(f"java_file: {java_file}")
    hidden_inputs = parse_hidden_inputs(payload)
    examples = parse_examples(payload)

    if not java_file:
        log("No java_file provided")
        print(json.dumps({"error": "No java_file provided"}, ensure_ascii=False))
        return

    java_path = Path(java_file)
    if not java_path.exists():
        log(f"Java file not found: {java_file}")
        print(json.dumps({"error": f"Java file not found: {java_file}"}, ensure_ascii=False))
        return

    # 1. 获取 Java 版本
    log("Getting Java version...")
    java_version = get_java_version()
    log(f"Java version: {java_version}")
    
    # 2. 读取源码
    log("Reading source code...")
    source_code = java_path.read_text(encoding="utf-8", errors="ignore")
    
    # 3. 查找类名
    log("Finding class name...")
    class_name = find_class_name(source_code)
    if not class_name:
        log("Could not find public class")
        print(json.dumps({"error": "Could not find public class in Java file"}, ensure_ascii=False))
        return
    log(f"Class name: {class_name}")

    # 4. 创建临时目录
    log("Creating temp directory...")
    temp_dir = Path(tempfile.mkdtemp())
    output_dir = temp_dir / "classes"
    output_dir.mkdir(exist_ok=True)

    try:
        # 5. 第一次编译
        log("Compiling Java file...")
        compile_success, compile_out, compile_err, current_source_path = compile_source_code(source_code, class_name, output_dir, temp_dir)
        current_source = source_code
        
        validation_error = ""
        if compile_success and examples:
            examples_ok, validation_error = validate_examples(class_name, output_dir, examples)
            compile_success = examples_ok
            if not examples_ok:
                compile_err = "Example validation failed:\n" + validation_error

        if not compile_success:
            log("Compilation or examples failed, using LLM to fix...")
            for fix_attempts in range(1, 3):
                fixed_code = fix_java_source(current_source, compile_err)
                fixed_class_name = find_class_name(fixed_code) or class_name
                
                compile_success, compile_out, compile_err, current_source_path = compile_source_code(fixed_code, fixed_class_name, output_dir, temp_dir)
                if compile_success:
                    class_name = fixed_class_name
                    if examples:
                        examples_ok, validation_error = validate_examples(class_name, output_dir, examples)
                        compile_success = examples_ok
                        if not examples_ok:
                            compile_err = "Example validation failed:\n" + validation_error
                    if compile_success:
                        log(f"Fixed and validated successfully on attempt {fix_attempts}")
                        break
                current_source = fixed_code
                log(f"Fix attempt {fix_attempts} failed, retrying...")

        if not compile_success:
            log("All compilation attempts failed")
            print(json.dumps({
                "error": "Compilation failed",
                "stderr": compile_err,
                "java_version": java_version,
            }, ensure_ascii=False))
            return
        
        log("Compilation successful")

        # 6. 执行 10 个隐藏用例
        log("Running test cases...")
        results = []
        for i, input_val in enumerate(hidden_inputs, 1):
            return_code, stdout, stderr = run_java(class_name, output_dir, input_val)
            if return_code == 0 and stdout:
                results.append(extract_numeric_output(stdout) or stdout)
            else:
                results.append("error")
            log(f"  Test {i}/10: input={input_val}, output={results[-1]}")

        log(f"Completed {len(results)} test cases")

        final_output = ",".join([java_version] + results)
        
        print(json.dumps({
            "answer": final_output,
            "java_version": java_version,
            "results": results,
        }, ensure_ascii=False))

    finally:
        # Cleanup
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


if __name__ == "__main__":
    main()