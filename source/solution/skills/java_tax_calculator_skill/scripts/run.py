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
    """获取 Java 版本号"""
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # 从 stderr 提取版本号，格式如 "21.0.11"
        output = result.stderr
        match = re.search(r'(\d+\.\d+\.\d+)', output)
        if match:
            return match.group(1)
        return "unknown"
    except Exception:
        return "unknown"


def find_class_name(file_path: Path) -> str | None:
    """Find the public class name in a Java file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("public class "):
                class_name = line.split()[2].split("{")[0].strip()
                return class_name
    except Exception:
        pass
    return None


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
    
    # 隐藏用例输入
    hidden_inputs = [
        "5000", "12000", "25000", "35000", "55000",
        "60000", "80000", "90000", "150000", "500000"
    ]

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
    class_name = find_class_name(java_path)
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
        compile_success, compile_out, compile_err = compile_java(java_path, output_dir)
        
        # 如果编译失败，使用大模型修复
        fix_attempts = 0
        if not compile_success:
            log("Compilation failed, using LLM to fix...")
            for fix_attempts in range(1, 3):
                fixed_code = fix_java_source(source_code, compile_err)
                fixed_path = temp_dir / java_path.name
                fixed_path.write_text(fixed_code, encoding="utf-8")
                
                # 再次编译
                compile_success, compile_out, compile_err = compile_java(fixed_path, output_dir)
                if compile_success:
                    log(f"Fixed and compiled successfully on attempt {fix_attempts}")
                    break
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
                # 格式化输出，保留两位小数
                try:
                    value = float(stdout)
                    results.append(f"{value:.2f}")
                except ValueError:
                    results.append(stdout)
            else:
                results.append("error")
            log(f"  Test {i}/10: input={input_val}, output={results[-1]}")

        log(f"Completed {len(results)} test cases")

        # 7. 格式化输出
        # 格式: contain[版本号],is[结果1],is[结果2],...
        output_parts = [f"contain[{java_version}]"]
        for r in results:
            output_parts.append(f"is[{r}]")
        
        final_output = ",".join(output_parts)
        
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