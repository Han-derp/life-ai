"""客服腔必须由**代码**删掉，不能指望提示词。

实测：人格文件里写着「不是客服式助手」，照样输出：

> 「我是你的助手，一个虚拟助手。我在这里是为了帮助你处理日常生活中的各种事务，
>   提供信息和建议。如果你有任何问题或需要帮助的地方，尽管告诉我。」

所以和 `workflows/tone.py` 同一个立场：**兜底要在代码里。**
"""

from app.services.tone_service import (
    ASSISTANT_TELLS,
    scrub_assistant_tone,
)

# 用户实测出现的那一整条（第一句 + 尾巴）
USER_REPORTED = (
    "我是你的助手，一个虚拟助手。我在这里是为了帮助你处理日常生活中的各种事务，"
    "提供信息和建议。如果你有任何问题或需要帮助的地方，尽管告诉我，"
    "我会尽力提供支持。"
)


def test_the_reported_reply_is_recognised_as_assistant_talk():
    """用户实测那条 —— 每一句都是客服腔，所以**没有一句能留下**。"""
    assert all(
        any(tell in sentence for tell in ASSISTANT_TELLS)
        for sentence in USER_REPORTED.split("。")
        if sentence.strip()
    )


def test_a_caring_reply_only_loses_its_tail():
    """★ 这才是最常见的形态：她真正想说的话留着，只掉尾巴上那句客服腔。"""
    reply = (
        "谢谢你，我挺喜欢和你聊天的。你最近是不是有点忙，所以晚上才这么晚睡？"
        "别太累了，记得早点休息，明天还有工作等着呢。"
        "如果你有其他问题或者需要讨论的事情，随时告诉我。我在这儿呢。"
    )

    scrubbed = scrub_assistant_tone(reply)

    assert "别太累了，记得早点休息" in scrubbed  # 真心话留着 ✓
    assert "随时告诉我" not in scrubbed  # 客服腔没了 ✓


def test_it_drops_every_sentence_that_is_assistant_talk():
    reply = "今天的日程我看到了。如果你需要帮助，随时联系我。晚上早点睡。"

    scrubbed = scrub_assistant_tone(reply)

    assert scrubbed == "今天的日程我看到了。晚上早点睡。"


def test_when_everything_is_assistant_talk_it_keeps_the_original():
    """全部被删时返回原文 —— "回一句空话"比留原文更糟（用户会以为坏了 ✗）。

    调用方据此记 warning，让这件事可被发现（见 `chat_service`）。
    """
    assert scrub_assistant_tone(USER_REPORTED) == USER_REPORTED


def test_it_does_not_touch_a_normal_reply():
    reply = "嗯。你这几天都在熬到两三点，白天肯定撑不住。"

    assert scrub_assistant_tone(reply) == reply


def test_an_empty_reply_stays_empty():
    assert scrub_assistant_tone("") == ""
    assert scrub_assistant_tone("   ") == ""


def test_punctuation_is_preserved_when_sentences_are_kept():
    """切句要保留标点，否则拼回去会变成一坨 ✗。"""
    assert scrub_assistant_tone("先睡觉。明天再说！") == "先睡觉。明天再说！"
