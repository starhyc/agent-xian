---
name: sensitive_info_scan
description: Recursively scan an archive (nested zip/tar, multi-level dirs) for sensitive info — phones, emails, ID cards, API keys — scanning text with regex and OCRing images, returning 'phone,email,idcard,apikey' counts.
---

# sensitive_info_scan

为「敏感信息扫描」这类题目设计：压缩包内含多层目录与 `.txt/.log/.tar/.png/.jpg` 等文件，需要统计敏感信息数量。

## 何时使用

题面给出压缩包，要求扫描其中所有文件并按类型汇总敏感信息数量时使用。

## 默认敏感信息类型

1. 手机号：以 1 开头的 11 位数字。
2. 邮箱：`user@domain.com` 形式。
3. 身份证号：18 位，末位可能为 X。
4. API Key：以 `sk-` 开头的字符串（长度不定）。

题面若增减类型或调整定义，以题面为准。

## 执行方式

直接 `skill_run`（参数可为空）。脚本会：

1. 递归解压压缩包（含嵌套 tar/zip、多层目录）。
2. 文本/日志文件用正则统计；图片文件用多模态模型转写可见文本后再用同样正则统计。
3. 汇总各类型出现次数。

## 返回格式

`手机号,邮箱,身份证,APIKey`，英文逗号分隔的四个数字（顺序与题面一致）。把输出作为最终答案原样返回。
