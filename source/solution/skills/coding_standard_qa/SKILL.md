---
name: coding_standard_qa
description: Answer questions strictly from coding-standard / specification documents, in the original document wording (not translated), returned joined by ';' in question order.
---

# coding_standard_qa

为「编程规范问答」这类题目设计：附件是一组规范文档（Java/Python/C++/JS-TS/Web 安全等），题面给出 Q1~Qn，需要严格依据规范原文作答。

## 何时使用

题面提供规范/标准文档目录，并按编号提出若干问题，要求依据文档内容作答、用分号分隔按序返回时使用。

## 关键约定（务必遵守）

- 答案必须来自规范文档原文。即使问题用英文提问，也按文档原文措辞回答，不要翻译。
- 问题数量、顺序、每个规范下的问题数都可能变化；严格按题面 Q1..Qn 的顺序作答。
- 每个答案尽量精确、简短，只给出规范要求的关键词/取值/原文表述，不要附加解释。
- 输出用英文分号 `;` 连接，数量与问题数一致。

## 执行方式

直接 `skill_run`（参数可为空）。脚本会自动读取声明目录下的全部规范文档，结合题面问题，逐题在原文中定位答案，按序用 `;` 拼接输出。把输出作为最终答案原样返回。
