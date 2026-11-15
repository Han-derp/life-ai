"""S4 记忆库：L3 印象（依据 / 态度 / 反例 / 代际）。

覆盖 DEVELOPMENT_PLAN v2.0 · S4 完成判据里的这几条：

- 每条 L3 能列出全部依据事件（不变量 5）
- 同类事件累积 3 次算"够格归纳"（触发线；本子步不含 LLM）
- 事实可删、**判断只能标异议**（MEMORY_DESIGN §4.3-C）

写印象文本的 LLM 归纳**不在本子步**，所以这里的印象都是手工创建的。
"""

import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.memory import ObservationCreate, TagCreate
from app.services import memory_service


def _tag(name: str = "写作", dimension: str = "pattern") -> dict:
    return memory_service.create_tag(TagCreate(name=name, dimension=dimension))


def _observation(
    content: str,
    source: str,
    tag_id: int | None = None,
    observed_at_local: str | None = None,
) -> dict:
    return memory_service.create_observation(
        ObservationCreate(
            content=content,
            source_text=source,
            tag_ids=[tag_id] if tag_id else [],
            observed_at_local=observed_at_local,
        )
    )


def _three_observations(tag_id: int) -> list[dict]:
    return [
        _observation(f"第 {i} 次说要写完却没动", f"我明天一定写完（{i}）", tag_id)
        for i in (1, 2, 3)
    ]


# ---------- 依据（不变量 5） ----------


def test_impression_requires_evidence():
    with pytest.raises(DomainValidationError):
        memory_service.create_impression("你写作启动困难", [])


def test_impression_lists_all_its_evidence():
    tag = _tag()
    observations = _three_observations(tag["id"])

    impression = memory_service.create_impression(
        "写作类任务启动困难，但周末例外",
        [o["id"] for o in observations],
        confidence=0.6,
    )

    assert impression["generation"] == 1
    assert impression["confidence"] == 0.6
    assert impression["status"] == "active"
    assert impression["attitude"] == "none"
    assert [e["id"] for e in impression["evidence"]] == [o["id"] for o in observations]
    # 依据必须能还原到原话（"点开看原文"）
    assert all(e["source_text"] for e in impression["evidence"])


def test_impression_rejects_unknown_evidence():
    with pytest.raises(NotFoundError):
        memory_service.create_impression("无依据", [999])


# ---------- 态度：认同 / 异议 / 撤回 ----------


def test_dispute_keeps_the_impression_but_marks_it():
    tag = _tag()
    observations = _three_observations(tag["id"])
    impression = memory_service.create_impression(
        "你容易拖延", [o["id"] for o in observations]
    )

    disputed = memory_service.set_impression_attitude(impression["id"], "disputed")

    assert disputed["attitude"] == "disputed"
    assert disputed["status"] == "disputed"
    assert disputed["content"] == "你容易拖延"  # 判断不消失
    assert len(disputed["evidence"]) == 3  # 依据也不消失


def test_agreement_is_recorded_separately_from_no_attitude():
    tag = _tag()
    observations = _three_observations(tag["id"])
    impression = memory_service.create_impression(
        "你对写作任务启动困难", [o["id"] for o in observations]
    )
    assert impression["attitude"] == "none"

    agreed = memory_service.set_impression_attitude(impression["id"], "agreed")

    assert agreed["attitude"] == "agreed"
    assert agreed["status"] == "active"


def test_invalid_attitude_is_rejected():
    tag = _tag()
    observations = _three_observations(tag["id"])
    impression = memory_service.create_impression("x", [o["id"] for o in observations])

    with pytest.raises(DomainValidationError):
        memory_service.set_impression_attitude(impression["id"], "deleted")


# ---------- 反例与代际（§4.4） ----------


def test_counterexamples_lower_confidence_then_ask_for_revision():
    tag = _tag()
    observations = _three_observations(tag["id"])
    impression = memory_service.create_impression(
        "你写作启动困难",
        [o["id"] for o in observations],
        confidence=0.7,
    )

    first = memory_service.record_counterexample(impression["id"])
    assert first["counterexamples"] == 1
    assert first["confidence"] == 0.7  # 第 1 次只标记，不动置信度
    assert first["needs_revision"] is False

    second = memory_service.record_counterexample(impression["id"])
    assert second["counterexamples"] == 2
    assert second["confidence"] == pytest.approx(0.5)  # 第 2 次降置信（0.7 → 0.5）
    assert second["needs_revision"] is False

    third = memory_service.record_counterexample(impression["id"])
    assert third["counterexamples"] == 3
    assert third["needs_revision"] is True  # 第 3 次该修订了
    assert third["content"] == "你写作启动困难"  # 但不自动改写判断


def test_counterexample_can_carry_an_observation_as_evidence():
    tag = _tag()
    observations = _three_observations(tag["id"])
    impression = memory_service.create_impression(
        "你写作启动困难", [o["id"] for o in observations]
    )

    weekend = _observation("周末一次就写完了", "今天写得很顺")
    updated = memory_service.record_counterexample(impression["id"], weekend["id"])

    assert weekend["id"] in [e["id"] for e in updated["evidence"]]


def test_revision_creates_a_new_generation_and_keeps_the_old_one():
    tag = _tag()
    observations = _three_observations(tag["id"])
    first = memory_service.create_impression(
        "你写作启动困难",
        [o["id"] for o in observations],
        confidence=0.4,
    )

    second = memory_service.revise_impression(
        first["id"],
        "启动困难在缓解，可能和任务清晰度有关",
        confidence=0.7,
    )

    assert second["generation"] == 2
    assert second["confidence"] == 0.7
    assert second["counterexamples"] == 0
    assert second["evidence"]  # 继承旧依据
    assert second["superseded_by"] is None

    old = memory_service.get_impression(first["id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == second["id"]
    assert old["content"] == "你写作启动困难"  # 旧版本永久保留

    # 默认列表能看到两代（这就是"演化史"）
    assert [i["id"] for i in memory_service.list_impressions()] == [
        second["id"],
        first["id"],
    ]
    assert [
        i["id"] for i in memory_service.list_impressions(include_superseded=False)
    ] == [second["id"]]


def test_superseded_generation_cannot_be_annotated():
    tag = _tag()
    observations = _three_observations(tag["id"])
    first = memory_service.create_impression("旧判断", [o["id"] for o in observations])
    memory_service.revise_impression(first["id"], "新判断")

    with pytest.raises(DomainValidationError):
        memory_service.set_impression_attitude(first["id"], "disputed")
    with pytest.raises(DomainValidationError):
        memory_service.record_counterexample(first["id"])


# ---------- 纠错：事实可删，判断只能标异议 ----------


def test_deleting_the_last_evidence_is_refused():
    """删掉最后一条依据会让印象答不出出处——不变量 5 不允许。"""
    tag = _tag()
    only = _observation("只此一次", "原话", tag["id"])
    impression = memory_service.create_impression("你总是这样", [only["id"]])

    with pytest.raises(DomainValidationError):
        memory_service.delete_observation(only["id"])

    assert memory_service.get_impression(impression["id"])["evidence"]


def test_deleting_one_of_several_evidence_items_is_allowed():
    tag = _tag()
    observations = _three_observations(tag["id"])
    impression = memory_service.create_impression(
        "你写作启动困难", [o["id"] for o in observations]
    )

    memory_service.delete_observation(observations[0]["id"])

    remaining = memory_service.get_impression(impression["id"])
    assert [e["id"] for e in remaining["evidence"]] == [
        observations[1]["id"],
        observations[2]["id"],
    ]
    assert remaining["content"] == "你写作启动困难"  # 判断没被删掉


# ---------- 素材触发线（§4.4） ----------


def test_supporting_observations_reports_readiness():
    tag = _tag()
    for i in (1, 2):
        _observation(f"第 {i} 次", f"原话 {i}", tag["id"])

    not_ready = memory_service.observations_supporting(tag["id"])
    assert not_ready["count"] == 2
    assert not_ready["ready"] is False

    _observation("第 3 次", "原话 3", tag["id"])

    ready = memory_service.observations_supporting(tag["id"])
    assert ready["count"] == 3
    assert ready["ready"] is True
