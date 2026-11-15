"""记忆检索（FTS5）—— DEVELOPMENT_PLAN 关键约束。

没有它，记忆只能按时间取最近 N 条：你今天说的话他能检索到，
**三个月前说的那句永远找不到**。而"他会记得你说过的事"这个承诺，
用三个月尺度去看，就是这个功能。

⚠️ 分词是这个功能最容易做错的地方（详见 migration 011 的注释）：
`unicode61` 会把一整串中文当成一个词；`trigram` 又要求命中**连续三个字符**
（「下个月要考试」与「考试的事情」不共享任何三字窗口 —— 恰恰是
最该命中的场景反而失手）。所以索引按"一字一 token"切、
查询用"相邻两字短语"匹配。下面第 2 条专门锁这个行为。
"""

from app.ai.context import assembler
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.memory import ObservationCreate
from app.services import memory_service

OLD = "2026-01-05T10:00:00"


def _observation(content: str, observed_at_local: str | None = None) -> dict:
    return memory_service.create_observation(
        ObservationCreate(
            content=content,
            source_text=f"我{content}",
            observed_at_local=observed_at_local,
        )
    )


def _filler(count: int) -> None:
    """一堆和查询无关的新记忆 —— 用来把老的那条挤出"最近 N 条"。"""
    for index in range(count):
        _observation(f"无关的第 {index} 件事")


# ---------- 核心：按相关度找到"最近 N 条"漏掉的旧事 ----------


def test_finds_an_old_observation_that_recency_would_miss(client):
    """★ 这就是整个功能存在的理由。"""
    _observation("下个月要考试", OLD)
    _filler(assembler.MEMORY_OBSERVATION_LIMIT + 2)

    # 按时间取最近 N 条：老的那条**不在**里面
    recent = memory_service.list_observations(limit=assembler.MEMORY_OBSERVATION_LIMIT)
    assert all("考试" not in item["content"] for item in recent)

    # 按相关度：找得到
    found = memory_service.search_memories("考试")["observations"]
    assert [item["content"] for item in found] == ["下个月要考试"]


def test_a_two_character_word_matches_across_different_contexts(client):
    """★★ trigram 会在这里失手：「下个月要考试」与「考试的事情」。

    两字词是中文的常态，所以这条决定了检索到底有没有用。
    """
    _observation("下个月要考试")

    found = memory_service.search_memories("考试的事情有点麻烦")["observations"]

    assert [item["content"] for item in found] == ["下个月要考试"]


def test_latin_words_are_kept_whole(client):
    """拉丁词不被逐字切开（DDL 是一个 token，不是 D/D/L）。"""
    _observation("集成测试 DDL 还没动")

    found = memory_service.search_memories("DDL 怎么办")["observations"]

    assert [item["content"] for item in found] == ["集成测试 DDL 还没动"]


def test_a_single_character_query_falls_back_to_like(client):
    """一个 token 拼不出短语 —— 退回 LIKE 扫描（基表，不是切开的索引）。"""
    _observation("今天很累")

    found = memory_service.search_memories("累")["observations"]

    assert [item["content"] for item in found] == ["今天很累"]


def test_no_match_returns_empty(client):
    _observation("下个月要考试")

    assert memory_service.search_memories("养猫的事情")["observations"] == []


def test_a_quote_in_the_query_does_not_break_the_match(client):
    """⚠️ 用户消息里一个双引号就能让 FTS5 抛语法错误 —— 整条链路崩掉。"""
    _observation("下个月要考试")

    found = memory_service.search_memories('他说"考试"这两个字')["observations"]

    assert [item["content"] for item in found] == ["下个月要考试"]


def test_an_empty_query_returns_nothing(client):
    _observation("下个月要考试")

    assert memory_service.search_memories("   ") == {
        "observations": [],
        "impressions": [],
    }


# ---------- 索引的维护 ----------


def test_impressions_are_searchable(client):
    """⚠️ FTS5 是**字面**检索 —— 这里用共享的字词，不假装它能懂同义词。

    （我第一版写的是「拖延的问题」，那是错的：它和「容易拖到最后一晚」
    一个字都不重叠，检索不到才是对的。）
    """
    observation = _observation("又一次拖到最后一晚")
    memory_service.create_impression(
        "写作类任务容易拖到最后一晚", [observation["id"]], confidence=0.7
    )

    found = memory_service.search_memories("他说我容易拖着不做")["impressions"]

    assert [item["content"] for item in found] == ["写作类任务容易拖到最后一晚"]


def test_deleting_an_observation_removes_it_from_the_index(client):
    observation = _observation("下个月要考试")
    assert memory_service.search_memories("考试")["observations"]

    memory_service.delete_observation(observation["id"])

    assert memory_service.search_memories("考试")["observations"] == []


def test_a_revised_impression_is_searchable_by_its_new_content(client):
    """改版之后：新版本能搜到，**旧版本也还在索引里**。

    旧版本是设计要求保留的（它是演化史的一部分，见 §7.3），
    所以它当然还搜得到 —— 但它已经被标成 ``superseded``，
    `_render_impressions()` 会跳过它，因此**进不了 prompt**。
    """
    observation = _observation("又一次拖到最后一晚")
    created = memory_service.create_impression(
        "写作类任务容易拖", [observation["id"]], confidence=0.7
    )

    memory_service.revise_impression(
        created["id"], "写作类任务其实挺稳的", [observation["id"]]
    )

    assert memory_service.search_memories("挺稳")["impressions"]

    old = memory_service.search_memories("容易拖")["impressions"]
    assert old
    assert old[0]["status"] == "superseded"


def test_backfill_indexes_rows_that_predate_the_index(client):
    """migration 011 建表**之前**就存在的记忆 —— 分词在 Python 里做，SQL 补不了。"""
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        entry = conn.execute(
            """
            INSERT INTO raw_entries (
                source_type, content, occurred_at, created_at, message_id
            ) VALUES ('manual', ?, ?, ?, NULL)
            """,
            ("我下个月要考试", now, now),
        )
        conn.execute(
            """
            INSERT INTO observations (
                content, observed_at, local_date, raw_entry_id, confidence,
                excluded_at, created_at, updated_at
            ) VALUES (?, ?, '2026-06-01', ?, 0.9, NULL, ?, ?)
            """,
            ("下个月要考试", now, entry.lastrowid, now, now),
        )

    assert memory_service.search_memories("考试")["observations"] == []

    assert memory_service.backfill_search_index() >= 1

    assert memory_service.search_memories("考试")["observations"]


# ---------- 接进上下文 ----------


def test_the_chat_prompt_carries_the_relevant_memory(client):
    _observation("下个月要考试", OLD)
    _filler(assembler.MEMORY_OBSERVATION_LIMIT + 2)

    plan = assembler.plan_for_request([], query="考试的事情得准备一下了")

    assert "下个月要考试" in plan.system_prompt


def test_without_a_query_it_falls_back_to_recency(client):
    """主动联系没有"用户这句话"，所以那边保持原来的行为：按时间取最近的。"""
    _observation("下个月要考试", OLD)
    _filler(assembler.MEMORY_OBSERVATION_LIMIT + 2)

    plan = assembler.plan_for_request([])

    # 没有查询词 → 按时间取最近 12 条：老的那条**漏掉**（这正是要有检索的原因），
    # 而**最后建的**那条填充在窗口内。
    assert "下个月要考试" not in plan.system_prompt
    assert f"无关的第 {assembler.MEMORY_OBSERVATION_LIMIT + 1} 件事" in plan.system_prompt
