"""S4 记忆库：L1/L2 与标签。

覆盖 DEVELOPMENT_PLAN v2.0 · S4 完成判据里本子步能做到的部分：

- 每条 L2 事件都带出处（不变量 5）
- 删标签后事件仍在（只剩未分类）
- 全部查询走 service 层（API 层没有 SQL）
"""

import pytest
from pydantic import ValidationError

from app.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from app.database.connection import get_connection
from app.schemas.memory import (
    ObservationCreate,
    ObservationTagsUpdate,
    TagCreate,
    TagUpdate,
)
from app.services import memory_service


def _make_tag(name: str, dimension: str = "state") -> dict:
    try:
        return memory_service.create_tag(TagCreate(name=name, dimension=dimension))
    except ConflictError:
        # migration 009 预置了默认标签（学习 / 焦虑 / 回避…），
        # 这些名字**可能已经存在** —— 复用现成的，别让测试撞在种子数据上。
        return next(tag for tag in memory_service.list_tags() if tag["name"] == name)


def _make_observation(content: str = "今天背了两小时单词", source: str = "今天背了两小时单词，有点累") -> dict:
    return memory_service.create_observation(
        ObservationCreate(content=content, source_text=source)
    )


# ---------- L2 观察与出处 ----------


def test_observation_always_carries_its_source():
    observation = _make_observation()

    assert observation["raw_entry_id"] is not None
    assert observation["source_text"] == "今天背了两小时单词，有点累"
    assert observation["content"] == "今天背了两小时单词"
    assert observation["confidence"] == 1.0
    assert observation["excluded"] is False


def test_observation_writes_l1_in_the_same_transaction():
    observation = _make_observation()

    with get_connection() as conn:
        entry = conn.execute(
            "SELECT content FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()

    assert entry is not None
    assert entry["content"] == "今天背了两小时单词，有点累"


def test_blank_source_is_rejected():
    """没有出处就不许存在（不变量 5）。纯空白也算没有出处。"""
    with pytest.raises(DomainValidationError):
        memory_service.create_observation(
            ObservationCreate(content="无出处的观察", source_text="   ")
        )


def test_too_many_tags_is_rejected_at_the_schema():
    with pytest.raises(ValidationError):
        ObservationCreate(content="x", source_text="y", tag_ids=list(range(11)))


def test_unknown_tag_id_is_rejected_and_writes_nothing():
    with pytest.raises(NotFoundError):
        memory_service.create_observation(
            ObservationCreate(content="x", source_text="y", tag_ids=[999])
        )

    assert memory_service.list_observations() == []


def test_observation_carries_tags_and_can_be_recategorised():
    focus = _make_tag("专注", "state")
    study = _make_tag("学习", "domain")

    observation = memory_service.create_observation(
        ObservationCreate(
            content="背单词 120 分钟",
            source_text="背了两小时单词",
            tag_ids=[focus["id"]],
        )
    )
    assert [t["name"] for t in observation["tags"]] == ["专注"]

    updated = memory_service.set_observation_tags(
        observation["id"],
        ObservationTagsUpdate(tag_ids=[study["id"]]),
    )
    assert [t["name"] for t in updated["tags"]] == ["学习"]


def test_list_observations_filters_by_date_and_tag():
    study = _make_tag("学习", "domain")
    first = memory_service.create_observation(
        ObservationCreate(
            content="甲",
            source_text="甲的原话",
            observed_at_local="2026-06-01T09:00",
            tag_ids=[study["id"]],
        )
    )
    memory_service.create_observation(
        ObservationCreate(
            content="乙",
            source_text="乙的原话",
            observed_at_local="2026-06-02T09:00",
        )
    )

    assert [o["id"] for o in memory_service.list_observations(local_date="2026-06-01")] == [
        first["id"]
    ]
    assert [o["id"] for o in memory_service.list_observations(tag_id=study["id"])] == [
        first["id"]
    ]


def test_invalid_local_date_is_a_domain_error_not_a_crash():
    """V1 遗留的缺陷模式：local_date 不校验直接 500。新端点不再犯。"""
    with pytest.raises(DomainValidationError):
        memory_service.list_observations(local_date="not-a-date")


def test_delete_observation_keeps_the_raw_entry():
    observation = _make_observation()
    memory_service.delete_observation(observation["id"])

    assert memory_service.list_observations() == []

    with get_connection() as conn:
        entry = conn.execute(
            "SELECT id FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()

    assert entry is not None  # L1 只追加、永不删除


# ---------- 标签 ----------


def test_tag_lifecycle():
    tag = _make_tag("焦虑", "state")
    assert tag["name"] == "焦虑"
    assert tag["observation_count"] == 0

    renamed = memory_service.update_tag(tag["id"], TagUpdate(name="紧张"))
    assert renamed["name"] == "紧张"

    memory_service.delete_tag(tag["id"])
    with pytest.raises(NotFoundError):
        memory_service.get_tag(tag["id"])


def test_duplicate_tag_name_is_rejected():
    """**直接调 service，不走 `_make_tag`。**

    那个 helper 为了绕开预置标签会**吞掉冲突**（migration 009 之后
    「学习」「焦虑」这些名字可能已经存在），用它就测不到这条了。
    所以这里用一个不在预置清单里的名字，让测试自给自足。
    """
    memory_service.create_tag(TagCreate(name="专注", dimension="state"))

    with pytest.raises(ConflictError):
        memory_service.create_tag(TagCreate(name="专注", dimension="state"))


def test_renaming_a_tag_onto_an_existing_name_is_rejected():
    first = _make_tag("学习", "domain")
    _make_tag("专注", "state")

    with pytest.raises(ConflictError):
        memory_service.update_tag(first["id"], TagUpdate(name="专注"))


def test_deleting_a_tag_keeps_the_observations():
    """完成判据：删标签后事件仍在（只剩未分类）。"""
    tag = _make_tag("回避", "pattern")
    observation = memory_service.create_observation(
        ObservationCreate(
            content="又一次推迟",
            source_text="我明天一定写完",
            tag_ids=[tag["id"]],
        )
    )

    memory_service.delete_tag(tag["id"])

    remaining = memory_service.list_observations()
    assert [o["id"] for o in remaining] == [observation["id"]]
    assert remaining[0]["tags"] == []
    assert remaining[0]["content"] == "又一次推迟"
    assert remaining[0]["source_text"] == "我明天一定写完"  # 出处也还在


def test_tag_observation_count_reflects_usage():
    tag = _make_tag("学习", "domain")
    memory_service.create_observation(
        ObservationCreate(content="甲", source_text="甲原话", tag_ids=[tag["id"]])
    )
    memory_service.create_observation(
        ObservationCreate(content="乙", source_text="乙原话", tag_ids=[tag["id"]])
    )

    assert memory_service.get_tag(tag["id"])["observation_count"] == 2
