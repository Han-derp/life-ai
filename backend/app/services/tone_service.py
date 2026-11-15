"""聊天回复的语气护栏：**逐句删掉客服腔**。

## 为什么这必须由代码做

`personas/*/persona.md` 第一句就是「不是客服式助手」，还专门写了一条
「不要使用客服式安慰套话」。模型可能不完全遵循提示词：

> 「我是你的助手，一个虚拟助手。我在这里是为了帮助你处理日常生活中的各种事务，
>   提供信息和建议。如果你有任何问题或需要帮助的地方，尽管告诉我。」

所以和 `workflows/tone.py` 同一个立场：**提示词写得再清楚也只是建议，
兜底要在代码里。** 那个模块管"主动开口不许有索取感"（整条作废），
这个模块管"聊天回复不许像客服"（逐句删）—— 两者都要，但归属层不同：
`services` 不能 import `workflows`，所以这一份放在这里。
"""

#: 命中即**删掉那一句**的客服腔标记。
#:
#: ⚠️ 粒度是"句"，不是"整条"。因为客服腔通常只挂在尾巴上
#: （"如果你有其他问题，随时告诉我"），整条删掉会把她真正想说的也弄没 ✗。
ASSISTANT_TELLS = (
    "虚拟助手",
    "人工智能助手",
    "AI 助手",
    "AI助手",
    "作为一个助手",
    "作为助手",
    "如果你有任何问题",
    "如果您有任何问题",
    "有任何需要",
    "还有其他需要",
    "还有什么可以帮",
    "随时告诉我",
    "随时联系我",
    "尽管告诉我",
    "我会尽力提供",
    "尽力为你提供",
    "提供信息和建议",
    "提供帮助和支持",
    "希望对你有所帮助",
    "希望这对你有帮助",
    "我会继续努力",
)

#: 句末标点（中英文都算）。切句时**保留标点**，拼回去才不丢。
_SENTENCE_ENDINGS = "。！？!?…\n"


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    current: list[str] = []
    for char in text:
        current.append(char)
        if char in _SENTENCE_ENDINGS:
            sentences.append("".join(current))
            current = []
    if current:
        sentences.append("".join(current))
    return sentences


def scrub_assistant_tone(text: str) -> str:
    """逐句删掉客服腔，返回清理后的文本。

    ⚠️ **全部被删时返回原文。** 那种情况说明整条都在打客服腔 ——
    此时"回一句空话"比留原文更糟（用户会以为坏了 ✗）。
    调用方应当据此记一条 warning，让这件事**可被发现**。
    """
    clean = (text or "").strip()
    if not clean:
        return clean

    kept = [
        sentence
        for sentence in _split_sentences(clean)
        if not any(tell in sentence for tell in ASSISTANT_TELLS)
    ]
    if not kept:
        return clean
    return "".join(kept).strip()
