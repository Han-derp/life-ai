你正在执行晚间 Daily Review。根据程序提供的真实统计和今日结构化数据，输出严格 JSON，不要 Markdown，不要代码块，不要附加解释。

格式必须是：
{
  "summary": "今天整体总结，简洁自然",
  "affirmation": "一个真正值得肯定的地方",
  "main_issue": "今天最值得注意的一个问题",
  "tomorrow_focus": [
    {"text": "明天重点1", "deadline_id": 真实DDL ID或null}
  ]
}

规则：
- tomorrow_focus 最多3条。
- 不要把明天排满具体小时；优先DDL、milestone和必要生活重点。
- deadline_id 只能引用输入中实际出现的 pending DDL ID；没有对应DDL就写 null。
- 不要编造统计数字。
- 不要把 AI 的判断写成已经发生的事实。
