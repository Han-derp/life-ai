"""S5「她眼中的我」：代际链聚合。

覆盖 DEVELOPMENT_PLAN v2.0 · S5 完成判据里**数据侧**的部分：

- 能看到她的判断以及它是怎么变的（代际链，从旧到新）
- 每条负面观察都能展开到具体日期和原话（依据带 source_text）

界面部分在 `frontend/js/her.js`；这里只保证它拿到的是对的形状。
"""

from app.schemas.memory import ObservationCreate, TagCreate
from app.services import memory_service


def _tag() -> dict:
    return memory_service.create_tag(TagCreate(name="写作", dimension="pattern"))


def _observation(content: str, source: str, tag_id: int | None = None) -> dict:
    return memory_service.create_observation(
        ObservationCreate(
            content=content,
            source_text=source,
            tag_ids=[tag_id] if tag_id else [],
        )
    )


def _lineage_of(impression_id: int) -> dict:
    for lineage in memory_service.list_impression_lineages():
        if lineage["current_id"] == impression_id:
            return lineage
    raise AssertionError(f"找不到 {impression_id} 所在的演化链")


def test_single_generation_lineage():
    tag = _tag()
    evidence = [_observation(f"第 {i} 次", f"原话 {i}", tag["id"]) for i in (1, 2, 3)]

    impression = memory_service.create_impression(
        "你写作启动困难",
        [item["id"] for item in evidence],
    )

    lineages = memory_service.list_impression_lineages()

    assert len(lineages) == 1
    lineage = lineages[0]
    assert lineage["current_id"] == impression["id"]
    assert lineage["generation"] == 1
    assert [entry["generation"] for entry in lineage["history"]] == [1]
    assert lineage["history"][0]["is_current"] is True


def test_revision_shows_up_as_history_in_chronological_order():
    tag = _tag()
    evidence = [_observation(f"第 {i} 次", f"原话 {i}", tag["id"]) for i in (1, 2, 3)]

    first = memory_service.create_impression(
        "你写作启动困难",
        [item["id"] for item in evidence],
    )
    second = memory_service.revise_impression(first["id"], "周末反而写得完")
    third = memory_service.revise_impression(second["id"], "可能和任务清晰度有关")

    lineage = _lineage_of(third["id"])

    assert lineage["generation"] == 3
    # 从旧到新——界面照这个顺序读成"我一开始…后来…现在…"
    assert [entry["content"] for entry in lineage["history"]] == [
        "你写作启动困难",
        "周末反而写得完",
        "可能和任务清晰度有关",
    ]
    assert [entry["is_current"] for entry in lineage["history"]] == [False, False, True]
    assert lineage["current"]["content"] == "可能和任务清晰度有关"


def test_lineage_exposes_evidence_with_dates_and_original_words():
    """完成判据：负面观察要能落到具体日期和原话。"""
    tag = _tag()
    target = _observation("说要写完却没动", "我明天一定写完", tag["id"])
    others = [_observation(f"第 {i} 次", f"原话 {i}", tag["id"]) for i in (2, 3)]

    impression = memory_service.create_impression(
        "你写作启动困难",
        [target["id"], *[item["id"] for item in others]],
    )

    entry = _lineage_of(impression["id"])["current"]["evidence"][0]

    assert entry["source_text"] == "我明天一定写完"  # 原话
    assert entry["local_date"]  # 日期
    assert entry["observed_at_local"]


def test_two_independent_lineages_stay_separate():
    tag = _tag()
    first_a = memory_service.create_impression(
        "判断A",
        [_observation("a1", "sa", tag["id"])["id"]],
    )
    first_b = memory_service.create_impression(
        "判断B",
        [_observation("b1", "sb", tag["id"])["id"]],
    )
    second_a = memory_service.revise_impression(first_a["id"], "判断A改")

    lineages = memory_service.list_impression_lineages()

    assert len(lineages) == 2
    assert _lineage_of(second_a["id"])["generation"] == 2
    assert _lineage_of(first_b["id"])["generation"] == 1
    assert [entry["content"] for entry in _lineage_of(first_b["id"])["history"]] == ["判断B"]


def test_annotations_survive_into_the_lineage():
    tag = _tag()
    impression = memory_service.create_impression(
        "你容易拖延",
        [_observation("a", "sa", tag["id"])["id"]],
    )
    memory_service.set_impression_attitude(impression["id"], "disputed")

    current = _lineage_of(impression["id"])["current"]

    assert current["attitude"] == "disputed"
    assert current["status"] == "disputed"
    assert current["content"] == "你容易拖延"  # 判断不消失
