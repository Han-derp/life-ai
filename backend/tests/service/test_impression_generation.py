"""印象归纳（L2 → L3）—— 记忆闭环的最后一块。

三条要守住的：

1. **必须有依据**（不变量 5）—— 一条没有事件的判断不许存在
2. **具体、不定性** —— 判断不能被写成标签（这条靠静态守提示词）
3. **同类靠标签** —— 自动路径只对"同标签、且还没被用过"的分组动手；
   全新安装没有标签时，自动归纳**本来就该什么都不做**

（照例显式 stub 模型调用 —— 不依赖"反正没配 Key 会走兜底"。）
"""

import json

import pytest

from app.core.exceptions import DomainValidationError
from app.schemas.memory import ObservationCreate, TagCreate
from app.services import memory_service
from app.workflows import impression


def _tag(name: str = "写作") -> dict:
    return memory_service.create_tag(TagCreate(name=name, dimension="pattern"))


def _observation(content: str, tag_id: int | None = None) -> dict:
    return memory_service.create_observation(
        ObservationCreate(
            content=content,
            source_text=f"我{content}",
            tag_ids=[tag_id] if tag_id else [],
        )
    )


def _stub(monkeypatch, payload) -> list[dict]:
    calls: list[dict] = []

    class StubProvider:
        def chat(self, messages, temperature=None, json_mode=False):
            calls.append({"task": "impression", "messages": messages, "json_mode": json_mode})
            return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(impression.factory, "get_provider", lambda task: StubProvider())
    return calls


def _judgement(content="写作类任务容易拖到最后一晚", confidence=0.6) -> dict:
    return {"content": content, "confidence": confidence}


# ---------- 归纳本身 ----------


def test_generates_an_impression_carrying_its_evidence(client, monkeypatch):
    first = _observation("说要写完却没动")
    second = _observation("又拖到最后一晚")
    _stub(monkeypatch, _judgement())

    created = impression.generate_from([first["id"], second["id"]])

    assert created["content"] == "写作类任务容易拖到最后一晚"
    assert created["confidence"] == 0.6
    # 不变量 5：判断必须列出依据
    assert len(created["evidence"]) == 2


def test_refuses_to_generate_without_evidence(client):
    """没有事件的判断不许存在。"""
    with pytest.raises(DomainValidationError):
        impression.generate_from([])


def test_no_judgement_when_the_model_sees_no_pattern(client, monkeypatch):
    """模型说"没有共同点"时**什么都不建** —— 这比编一条强得多。"""
    observation = _observation("背了两小时单词")
    _stub(monkeypatch, {"content": None, "confidence": 0})

    assert impression.generate_from([observation["id"]]) is None
    assert memory_service.list_impressions(include_superseded=False) == []


def test_malformed_json_is_survivable(client, monkeypatch):
    observation = _observation("背了两小时单词")
    _stub(monkeypatch, "这根本不是 JSON")

    assert impression.generate_from([observation["id"]]) is None


def test_json_mode_and_the_impression_task_are_used(client, monkeypatch):
    """印象走云端（`impression` 任务）—— 本地 3B 做不了多事件推理。"""
    observation = _observation("背了两小时单词")
    calls = _stub(monkeypatch, _judgement())

    impression.generate_from([observation["id"]])

    assert calls[0]["task"] == "impression"
    assert calls[0]["json_mode"] is True


def test_the_batch_carries_dates_and_ids(client, monkeypatch):
    observation = _observation("背了两小时单词")
    calls = _stub(monkeypatch, _judgement())

    impression.generate_from([observation["id"]])

    batch = calls[0]["messages"][-1]["content"]
    assert f"[{observation['id']}]" in batch
    assert observation["local_date"] in batch


# ---------- 同类靠标签 ----------


def test_a_group_needs_a_tag(client, monkeypatch):
    """**三条没有标签的观察凑不成"同类"** —— 这是设计的前置依赖。"""
    for index in range(3):
        _observation(f"第 {index} 次说要写完却没动")

    assert impression.ripe_candidates() == []


def test_a_tagged_group_becomes_a_candidate(client):
    tag = _tag()
    for index in range(3):
        _observation(f"第 {index} 次说要写完却没动", tag["id"])

    candidates = impression.ripe_candidates()

    assert len(candidates) == 1
    assert candidates[0]["tag_name"] == "写作"
    assert len(candidates[0]["observation_ids"]) == 3


def test_a_group_below_the_threshold_is_not_ripe(client):
    tag = _tag()
    for index in range(2):
        _observation(f"第 {index} 次说要写完却没动", tag["id"])

    assert impression.ripe_candidates() == []


def test_used_observations_are_not_distilled_twice(client, monkeypatch):
    """否则每跑一次就会多出一条重复印象。"""
    tag = _tag()
    ids = [_observation(f"第 {index} 次没动", tag["id"])["id"] for index in range(3)]
    _stub(monkeypatch, _judgement())

    impression.generate_pending()
    assert impression.ripe_candidates() == []

    # 又来了一条新的，还是不够 3 条
    _observation("第 4 次没动", tag["id"])
    assert impression.ripe_candidates() == []


def test_generate_pending_reports_what_it_created(client, monkeypatch):
    tag = _tag()
    for index in range(3):
        _observation(f"第 {index} 次没动", tag["id"])
    _stub(monkeypatch, _judgement())

    counts = impression.generate_pending()

    assert counts == {"created": 1, "supported": 0, "countered": 0, "revised": 0}
    saved = memory_service.list_impressions(include_superseded=False)
    assert saved[0]["content"] == "写作类任务容易拖到最后一晚"


# ---------- API ----------


def test_api_explains_why_it_skipped(client):
    """自动路径什么都没做时，必须说清**为什么** —— 否则用户只会觉得坏了。"""
    body = client.post("/api/v1/memory/impression-generate", json={}).json()

    assert body["generated"] == 0
    assert "标签" in body["skipped_reason"]


def test_api_generates_from_explicit_ids(client, monkeypatch):
    first = _observation("说要写完却没动")
    second = _observation("又拖到最后一晚")
    _stub(monkeypatch, _judgement())

    body = client.post(
        "/api/v1/memory/impression-generate",
        json={"observation_ids": [first["id"], second["id"]]},
    ).json()

    assert body["generated"] == 1
    assert body["skipped_reason"] is None


def test_candidates_endpoint_lists_ripe_tags(client):
    tag = _tag("作息")
    for index in range(3):
        _observation(f"第 {index} 次熬到两点", tag["id"])

    body = client.get("/api/v1/memory/impression-candidates").json()

    assert [item["tag_name"] for item in body] == ["作息"]


# ---------- 静态守提示词 ----------


def test_the_prompt_forbids_turning_judgements_into_labels():
    """归纳最危险的失败模式：把"行为的形状"写成"人的类型"（R3）。"""
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("impression.md")

    assert "具体、不定性" in prompt
    assert "标签" in prompt
    # 置信度档位必须写死在提示词里，否则它会随手给高分
    assert "0.40" in prompt


# ---------- 设计裁决 R6：证据的**方向** ----------


def _three(tag_id: int, prefix: str = "没动") -> list[int]:
    return [_observation(f"{prefix} {index}", tag_id)["id"] for index in range(3)]


def _directions(ids: list[int], direction: str) -> dict:
    return {
        "directions": [
            {"observation_id": oid, "direction": direction} for oid in ids
        ]
    }


def test_same_direction_evidence_does_not_create_a_new_generation(client, monkeypatch):
    """★★ R6 的关键修正。

    没有它，你说"我不想动"第三百次时她会生成"印象 #47"、内容还是"你最近有点累" ——
    那不是记忆，是噪声；而且"她怎么改了看法"（§7.3）会退化成同一个结论的重复列表。
    """
    tag = _tag()
    _three(tag["id"])
    _stub(monkeypatch, _judgement())
    impression.generate_pending()  # 创建 #1

    more = _three(tag["id"], "又没动")
    _stub(monkeypatch, _directions(more, "support"))
    counts = impression.generate_pending()

    assert counts["created"] == 0  # ← 没有再创建新一代
    assert counts["supported"] == 1

    saved = memory_service.list_impressions(include_superseded=False)
    assert len(saved) == 1
    assert saved[0]["generation"] == 1  # 代际没涨
    assert len(saved[0]["evidence"]) == 6  # 但依据涨到了 6 条


def test_counter_evidence_is_recorded_and_marks_needs_revision(client, monkeypatch):
    """R6 的第三条路：反方向 → 1 次标记 / 2 次降置信 / 3 次修订。"""
    tag = _tag()
    _three(tag["id"])
    _stub(monkeypatch, _judgement())
    impression.generate_pending()

    fresh = _three(tag["id"], "反而提前交了")
    _stub(monkeypatch, _directions(fresh, "counter"))
    counts = impression.generate_pending()

    assert counts["countered"] == 3
    assert counts["created"] == 0

    saved = memory_service.list_impressions(include_superseded=False)[0]
    assert saved["counterexamples"] == 3
    assert saved["needs_revision"] is True
    assert saved["generation"] == 1  # 反例**不自动**改写内容
    assert saved["confidence"] < 0.6  # 第 2 次反例起下调


def test_a_failed_direction_judgement_defaults_to_support(client, monkeypatch):
    """判不出方向时必须走**保守**的那一侧 —— 误判反例的代价大得多。"""
    tag = _tag()
    _three(tag["id"])
    _stub(monkeypatch, _judgement())
    impression.generate_pending()

    _three(tag["id"], "不确定")
    _stub(monkeypatch, "这根本不是 JSON")
    counts = impression.generate_pending()

    assert counts["countered"] == 0
    assert counts["supported"] == 1


def test_a_counter_can_flip_a_previous_support(client, monkeypatch):
    """同一条事件先 support、后改判 counter 时必须能**翻过来**。

    否则反例会被 `ON CONFLICT DO NOTHING` 静默吞掉 —— 她永远不会改看法。
    """
    from app.database.connection import get_connection

    tag = _tag()
    observation = _observation("说要写却没动", tag["id"])
    created = memory_service.create_impression(
        "写作类任务容易拖", [observation["id"]], confidence=0.6
    )

    memory_service.add_supporting_evidence(created["id"], [observation["id"]])
    memory_service.record_counterexample(created["id"], observation["id"])

    with get_connection() as conn:
        relation = conn.execute(
            """
            SELECT relation FROM impression_evidence
            WHERE impression_id = ? AND observation_id = ?
            """,
            (created["id"], observation["id"]),
        ).fetchone()["relation"]

    assert relation == "counter"


def test_the_direction_prompt_prefers_support_when_unsure():
    """**静态**守提示词：默认值必须是保守的那一侧。"""
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("evidence_direction.md")

    assert "拿不准就判" in prompt
    assert "误判成反例" in prompt
