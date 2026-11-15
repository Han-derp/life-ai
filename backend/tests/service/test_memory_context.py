"""S9 的前置：记忆必须真的进 prompt。

S1 在 assembler 里预留了 ``PRIORITY_MEMORY_HIGH`` / ``PRIORITY_MEMORY_LOW``
和 ``KIND_MEMORY``，但一直没接上——那意味着她回答时**看不到记忆**，
S9 的完成判据"回复体现记忆内容"就是空的。这个文件锁住那块。
"""

from app.ai.context import assembler
from app.ai.context.blocks import KIND_MEMORY, SENSITIVITY_SENSITIVE
from app.schemas.memory import ObservationCreate
from app.services import memory_service


def _observation(content: str = "她说下个月要考试") -> dict:
    return memory_service.create_observation(
        ObservationCreate(content=content, source_text="我下个月要考试，有点慌")
    )


def _impression(observation_id: int, content: str, confidence: float) -> dict:
    return memory_service.create_impression(
        content,
        [observation_id],
        confidence=confidence,
    )


def _memory_blocks() -> dict:
    return {
        block.id: block
        for block in assembler.get_context_blocks()
        if block.kind == KIND_MEMORY
    }


def test_memory_blocks_are_absent_when_there_is_no_memory():
    """全新安装不该出现空块——顺带保证块列表和 S1/S2 时代一致。"""
    assert _memory_blocks() == {}


def test_observations_become_a_context_block():
    observation = _observation()

    blocks = _memory_blocks()

    assert "memory:observations" in blocks
    assert observation["content"] in blocks["memory:observations"].content


def test_impressions_become_a_context_block():
    observation = _observation()
    _impression(observation["id"], "你最近压力很大", 0.8)

    blocks = _memory_blocks()

    assert "memory:impressions" in blocks
    content = blocks["memory:impressions"].content
    assert "你最近压力很大" in content
    assert "0.8" in content  # 置信度要一起给她，她才知道该说多肯定
    assert "依据 1 条" in content


def test_low_confidence_impressions_stay_out_of_the_prompt():
    """她不该拿自己瞎猜的东西当依据说话。"""
    observation = _observation()
    _impression(observation["id"], "你可能不喜欢这份工作", 0.2)

    assert "memory:impressions" not in _memory_blocks()


def test_disputed_impressions_must_say_so():
    """不变量 6：判断可以标异议，但不能装作没有异议。"""
    observation = _observation()
    impression = _impression(observation["id"], "你最近压力很大", 0.8)
    memory_service.set_impression_attitude(impression["id"], "disputed")

    content = _memory_blocks()["memory:impressions"].content

    assert "异议" in content


def test_memory_blocks_are_marked_sensitive():
    """她记得的你，是整份上下文里最私密的一块。"""
    _observation()

    memory_blocks = [
        block
        for block in assembler.get_context_blocks()
        if block.kind == KIND_MEMORY
    ]

    assert memory_blocks
    for block in memory_blocks:
        assert block.sensitivity == SENSITIVITY_SENSITIVE


def test_memory_sits_between_rules_and_today():
    """顺序＝变化频率从低到高；稳定的前缀才是 KV 缓存命中的前提。"""
    _observation()

    ids = [block.id for block in assembler.get_context_blocks()]

    assert ids.index("rules") < ids.index("memory:observations")
    assert ids.index("memory:observations") < ids.index("today:meta")


def test_memory_blocks_can_be_excluded():
    """能进上下文，就必须能被排除——否则 S2 那个面板管不住最私密的一块。"""
    _observation()

    for block in assembler.get_context_blocks():
        assert block.state == "include"
