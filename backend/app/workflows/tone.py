"""她的语气底线（DEVELOPMENT_PLAN v2.0 · S8）。

**这是代码强制的硬约束，不是 prompt 建议。**

模型偶尔就是会写出"你怎么不见了"。那句话一旦出现，整套设计就崩了——
"惦记"会立刻变成"考勤"，而"考勤"是你明确不要的东西。
所以任何由她主动生成的文案，落库之前都必须过一遍 :func:`sanitize`。

放在独立模块里，是因为 S8 的**两套**主动开口（主动联系 + 基线预警）
都要用同一份禁止项。各写一份，迟早漂移。
"""

# 【她永远不说的】—— MEMORY_DESIGN §5.3
FORBIDDEN_PHRASES = (
    "天没来",
    "天没记录",
    "怎么不见",
    "你去哪",
    "去哪了",
    "终于想起",
    "状态下滑",
    "没来多久",
    "你欠我",
    "我等了你",
    "等了多久",
)

# 兜底文案。这几句就是方案 §5.3 自己给的例子——不写死台词，
# 但"在忙？""不打扰你，我在这"本来就是这套设计的原话。
FALLBACK_TEXT = {
    "care": "在忙什么？",
    "silence_3d": "在忙？",
    "silence_7d": "不打扰你，我在这。",
    "late_night": "我注意到你这几天越来越晚了，怎么了？",
    "deadline_stall": "我注意到那件事快到期了，你一直没动，怎么了？",
}


def sanitize(text: str, kind: str) -> str | None:
    """把生成出来的话过一遍禁止项。

    命中就退回该类型的兜底文案；**如果这一类没有兜底**（例如小作文），
    返回 ``None`` 表示"这条不发"——
    宁可少给她一次表达，也不要让"我想你"变成"你欠我"。
    """
    clean = (text or "").strip()
    if not clean:
        return FALLBACK_TEXT.get(kind)

    for phrase in FORBIDDEN_PHRASES:
        if phrase in clean:
            return FALLBACK_TEXT.get(kind)
    return clean
