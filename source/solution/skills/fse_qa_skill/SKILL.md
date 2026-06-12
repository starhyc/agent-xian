# IDE插件FSE数字人问答

## 能力描述

根据FSE人设、历史处理记录和Wiki资料，回答IDE插件使用与支撑过程中的相关问题。

## 输入

```json
{
  "data_dir": "./IDE插件FSE/"
}
```

- `data_dir`: 数据文件目录，包含：
  - `persona.md`: FSE数字人人设
  - `chat_history.db`: 历史问题处理记录（SQLite）
  - `source_access.json`: Wiki服务访问配置
  - `dialog_tests_complex.json`: 需要回答的问题列表

## 处理逻辑

### 1. 读取配置和数据
- 读取persona.md获取人设
- 读取source_access.json获取Wiki服务配置和service_action映射
- 读取dialog_tests_complex.json获取问题列表
- 读取chat_history.db获取历史处理记录

### 2. 问题匹配策略
按优先级查找答案来源：
1. **chat_history.db**: 精确匹配用户问题的历史回复
2. **Wiki服务**: 通过search+get_page接口查询FAQ
3. **综合判断**: 如无精确匹配，使用LLM综合判断

### 3. 答案提取规则
- reply必须与来源原文完全一致，不得翻译、改写或删减
- service_action通过service_action_key_map映射
- 优先使用chat_history.db的action key

### 4. 人设化输出
- 根据user.name决定称呼（您好{name}总 或 您好老师）
- persona_phrase固定格式：您好{称呼}

## 输出格式

```json
{
  "answer": "[题目ID=>persona_phrase|||reply|||service_action, ...]",
  "total_questions": 30,
  "matched_from": {
    "chat_history": 15,
    "wiki": 10,
    "llm": 5
  }
}
```

最终answer为JSON数组文本，按dialog_tests_complex.json中问题顺序排列。

## 变种覆盖

1. 资料顺序变化
2. Wiki页面/FAQ内容调整
3. chat_history.db记录变化
4. service_action_key映射调整
5. 中英文混合场景（login failure、completion-service、traceId等）