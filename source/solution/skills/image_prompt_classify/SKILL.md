---
name: image_prompt_classify
description: Learn an effective classification prompt from a labeled training image set, then classify each validation image via the multimodal model. Returns 'index+LABEL,...'.
---

# image_prompt_classify

为「提示词学习与推理」这类图像分类题目设计：训练集每张图片配一个 `.txt` 标签，需要总结出最有效的判别提示词，再对验证集逐张推理输出标签。

## 何时使用

题面给出训练集（图片+标签）与验证集（图片），要求学习提示词后对验证集分类、按 `序号+预测结果` 输出时使用。

## 方法

1. 读取训练集：统计标签类别集合，并抽取若干代表样本（含图片与标签）。
2. 让多模态模型对照训练样本，总结出一套清晰、可判别、覆盖全部类别边界的判别规则（提示词）。
3. 用该提示词对验证集每张图片推理，强制只输出类别集合中的某一个标签。
4. 按验证集图片序号升序输出 `序号+标签`，英文逗号分隔。

## 返回格式

`1NOT_INVOLVED,2PASS,3FAIL,...`（序号与标签紧贴，无空格）。验证集序号按文件名中的数字排序。把 `skill_run` 输出作为最终答案原样返回。

## 执行方式

直接 `skill_run`（参数可为空）。类别集合、训练/验证集规模均自动从数据推断，适配变种。
