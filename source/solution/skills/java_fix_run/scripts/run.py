from __future__ import annotations

import base64
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from source.solution.lib import skillio


def _triple_b64(s: str) -> str:
    x = s
    for _ in range(3):
        x = base64.b64decode(x).decode("utf-8", errors="replace")
    return x


def _decode_threshold(src: str):
    m = re.search(r'DEDUCTION_POINT_ENCODED\s*=\s*"([^"]+)"', src)
    if not m:
        return None
    try:
        return float(re.search(r"-?\d+", _triple_b64(m.group(1))).group(0))
    except Exception:
        return None


def _decode_brackets(src: str):
    m = re.search(r'TAX_BRACKETS_ENCODED\s*=\s*"([^"]+)"', src)
    if not m:
        return None
    try:
        decoded = _triple_b64(m.group(1))
        nums = re.findall(r"-?\d+(?:\.\d+)?", decoded)
        vals = [float(n) for n in nums]
        rows = [vals[i:i + 4] for i in range(0, len(vals), 4)]
        return [r for r in rows if len(r) == 4]
    except Exception:
        return None


def _compute(salary: float, threshold: float, brackets):
    taxable = salary - threshold
    if taxable <= 0:
        return 0.0
    for lower, upper, rate, deduction in brackets:
        if lower <= taxable <= upper:
            return taxable * rate - deduction
    # Above the top bracket: use the last row's rate/deduction.
    lower, upper, rate, deduction = brackets[-1]
    return taxable * rate - deduction


def _parse_inputs(qtext: str):
    inputs = []
    lines = qtext.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if "隐藏用例" in ln:
            start = i
            break
    region = lines[start + 1:] if start is not None else lines
    for ln in region:
        s = ln.strip()
        if re.fullmatch(r"-?\d+", s):
            inputs.append(int(s))
        elif ("返回格式" in ln or "格式示例" in ln) and inputs:
            break
    return inputs


def _java_version():
    try:
        out = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=30)
        text = (out.stderr or "") + (out.stdout or "")
        for line in text.splitlines():
            if "version" in line.lower():
                return line.strip()
        return text.strip().splitlines()[0] if text.strip() else "unknown"
    except Exception as exc:  # noqa: BLE001
        return "java -version failed: %s" % exc


def _fmt(x: float) -> str:
    return "%.2f" % (x + 0.0)


def main() -> None:
    skillio.read_stdin_args()
    src_files = skillio.list_files([".java"]) or skillio.list_files()
    src = ""
    for f in src_files:
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "class" in t:
            src = t
            break
    threshold = _decode_threshold(src)
    brackets = _decode_brackets(src)
    inputs = _parse_inputs(skillio.question_text())

    version = _java_version()
    results = []
    if threshold is not None and brackets:
        for sal in inputs:
            results.append(_fmt(_compute(float(sal), threshold, brackets)))

    skillio.emit(",".join([version] + results))


if __name__ == "__main__":
    main()
