from app.core.token_budget import estimate_tokens


def test_estimate_tokens_empty_string():
    assert estimate_tokens("") == 0


def test_estimate_tokens_cjk_counts_one_per_char():
    # 4 CJK chars -> 4 tokens, plus the non-CJK floor of max(1, ceil(0/4))
    # applied by estimate_tokens even when there is no non-CJK text.
    assert estimate_tokens("你好世界") == 5


def test_estimate_tokens_ascii_counts_roughly_one_per_four_chars():
    # 16 ascii chars -> ceil(16/4) = 4
    assert estimate_tokens("a" * 16) == 4


def test_estimate_tokens_mixed_content():
    text = "你好" + "abcd"
    # 2 CJK tokens + ceil(4/4)=1 ascii token
    assert estimate_tokens(text) == 3


# ⚠️ 这里原来还有 6 条测试，测的是 `truncate_text()` / `take_recent_messages()`。
# **2026-10-04 按设计裁决 R9 一起删了**（连实现带测试）——
# 那两个函数会从句子中间硬切，是被方案否决的模式 ✓。
# 详见 `app/core/token_budget.py` 末尾的说明。

