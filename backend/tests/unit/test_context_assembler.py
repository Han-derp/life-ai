"""S1 上下文分块：块顺序、整块丢弃、人格不被截断。

对应 DEVELOPMENT_PLAN v2.0 · S1 的完成判据：

- persona_core.md 最后一行不再被截断
- 超预算时丢掉的是**完整的块**，不出现句子中间的「…」
- ``now`` 移出 system prompt
- 块顺序稳定（利于 KV 缓存命中）
"""

from app.ai.context import assembler
from app.ai.context.blocks import (
    KIND_PERSONA,
    KIND_RULES,
    KIND_TODAY,
    STATE_EXCLUDE_ALWAYS,
    STATE_INCLUDE,
    ContextBlock,
)
from app.core.prompt_loader import load_prompt
from app.core.token_budget import estimate_tokens

EXPECTED_ORDER = [
    "persona",
    "rules",
    "today:meta",
    "today:schedule",
    "today:deadlines",
    "today:habits",
    "today:pomodoro",
    "today:timeline",
]


def _blk(block_id: str, kind: str, content: str, priority: int, state: str = STATE_INCLUDE) -> ContextBlock:
    """构造一个测试用块，token 按生产口径估算。"""
    return ContextBlock(
        id=block_id,
        kind=kind,
        label=block_id,
        content=content,
        tokens=estimate_tokens(content),
        state=state,
        priority=priority,
    )


# ---------- 块结构 ----------


def test_context_blocks_have_stable_order():
    ids = [b.id for b in assembler.get_context_blocks()]
    assert ids == EXPECTED_ORDER


def test_context_block_kinds_and_token_estimates():
    blocks = assembler.get_context_blocks()

    assert blocks[0].kind == KIND_PERSONA
    assert blocks[1].kind == KIND_RULES
    assert all(b.kind == KIND_TODAY for b in blocks[2:])
    assert all(b.tokens == estimate_tokens(b.content) for b in blocks)
    assert all(b.state == STATE_INCLUDE for b in blocks)


def test_persona_and_rules_are_never_truncated():
    """S1 判据：人格最后一行不能被切掉。"""
    blocks = {b.id: b for b in assembler.get_context_blocks()}

    # S3 起人格来自 personas/<当前人格>/persona.md，不再是 app/prompts/persona_core.md
    from app.core.persona_loader import load_persona_text
    from app.services.persona_service import active_persona_name

    assert blocks["persona"].content == load_persona_text(active_persona_name())
    assert blocks["rules"].content == load_prompt("behavior_rules.md")
    # ⚠️ 原来断言 `"…" not in content` ✗ —— 那是拿**省略号**当"被截断"的标记，
    # 可省略号本来就可能出现在人格正文里（"作为一个……"），
    # 于是用户正常改人格就会红 ✗。
    #
    # 改成检查**真正的截断形态**：文件最后一行必须完整出现在块里 ——
    # 这才是这条判据（"人格最后一行不能被切掉"）真正要说的事。
    last_line = load_persona_text(active_persona_name()).strip().splitlines()[-1]
    assert last_line.strip() in blocks["persona"].content


def test_now_is_not_sent_to_model():
    """S1：精确 UTC 时间移出 system prompt，只保留当地日期。"""
    prompt = assembler.assemble(assembler.get_context_blocks())

    assert "当前UTC时间" not in prompt
    assert "用户当地日期：" in prompt


def test_prompt_starts_with_persona_for_cache_stability():
    blocks = assembler.get_context_blocks()
    prompt = assembler.assemble(blocks)

    assert prompt.startswith(blocks[0].content)
    # 人格在规则之前，前缀顺序稳定。
    assert prompt.index(blocks[0].content) < prompt.index(blocks[1].content)


def test_default_budget_always_keeps_identity():
    """默认预算下人格与规则必须保留——她至少知道自己是谁。"""
    blocks = assembler.get_context_blocks()
    prompt = assembler.assemble(blocks)

    assert blocks[0].content in prompt  # persona
    assert blocks[1].content in prompt  # rules


# ---------- 整块丢弃 ----------


def test_assemble_drops_lowest_priority_block_first():
    persona = _blk("persona", KIND_PERSONA, "人格" * 20, assembler.PRIORITY_PERSONA)
    schedule = _blk("today:schedule", KIND_TODAY, "日程" * 20, assembler.PRIORITY_TODAY_SCHEDULE)
    timeline = _blk("today:timeline", KIND_TODAY, "时间轴" * 20, assembler.PRIORITY_TODAY_TIMELINE)

    prompt = assembler.assemble(
        [persona, schedule, timeline],
        budget=persona.tokens + schedule.tokens,
    )

    assert persona.content in prompt
    assert schedule.content in prompt
    assert timeline.content not in prompt


def test_assemble_drops_whole_blocks_without_mid_sentence_cut():
    """丢的是整块：低优先级块要么整体在，要么整体不在，不留半截。"""
    persona = _blk("persona", KIND_PERSONA, "人格" * 20, assembler.PRIORITY_PERSONA)
    timeline = _blk("today:timeline", KIND_TODAY, "时间轴" * 20, assembler.PRIORITY_TODAY_TIMELINE)

    prompt = assembler.assemble([persona, timeline], budget=persona.tokens)

    assert prompt.strip() == persona.content
    assert "…" not in prompt
    assert "时间轴" not in prompt


def test_assemble_keeps_persona_and_rules_even_over_budget():
    """人格与规则最后丢：极端预算下宁可略超，也不丢"她是谁"。"""
    persona = _blk("persona", KIND_PERSONA, "人格" * 20, assembler.PRIORITY_PERSONA)
    rules = _blk("rules", KIND_RULES, "规则" * 20, assembler.PRIORITY_RULES)
    timeline = _blk("today:timeline", KIND_TODAY, "时间轴" * 20, assembler.PRIORITY_TODAY_TIMELINE)

    prompt = assembler.assemble([persona, rules, timeline], budget=1)

    assert persona.content in prompt
    assert rules.content in prompt
    assert timeline.content not in prompt


def test_assemble_keeps_a_lone_persona_under_zero_budget():
    persona = _blk("persona", KIND_PERSONA, "人格", assembler.PRIORITY_PERSONA)

    assert assembler.assemble([persona], budget=0) == persona.content


def test_assemble_skips_excluded_blocks():
    persona = _blk("persona", KIND_PERSONA, "人格", assembler.PRIORITY_PERSONA)
    timeline = _blk(
        "today:timeline",
        KIND_TODAY,
        "时间轴",
        assembler.PRIORITY_TODAY_TIMELINE,
        state=STATE_EXCLUDE_ALWAYS,
    )

    prompt = assembler.assemble([persona, timeline], budget=1000)

    assert persona.content in prompt
    assert timeline.content not in prompt
