import re


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """Cheap V1 estimate: CJK ~= 1 token/char, others ~= 1 token/4 chars."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + max(1, (other + 3) // 4)


# ⚠️ 这里原来还有两个函数：`truncate_text()` 和 `take_recent_messages()`。
# **2026-10-04 按设计裁决 R9 删除。**
#
# 它们会**从句子中间硬切**（`text[:max_tokens].rstrip() + "…"`）✗ ——
# 而方案早就否决了那个模式：`assembler` 现在是**整块丢弃** ✓，
# 宁可少给她一段上下文，也不给她半句话。
#
# 留着它们的坏处不是占地方，是**下一个人会照着写** ✗ ——
# 一个被否决的模式，以"现成的函数"的形式躺在核心模块里，等于在推荐它。

