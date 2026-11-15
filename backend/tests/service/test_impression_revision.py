"""反例攒够 3 次 → **自动改写**（设计裁决 R6 第三条路）。

这条以前是缺的，而且缺得很难看：`needs_revision` 会亮，界面上也写着
"他该重新想一遍了（等下一次归纳时他会改写这条判断）"，
但**没有任何东西真的去改** —— 那句界面文案是空头承诺。

要守住三件事：
1. 只有攒够反例的才改（2 次不动）
2. 反例必须**被吸收进**新判断（提示词里要能看见它们）
3. 失败可重试、成功不重复（幂等）
"""

import json

from app.core.exceptions import ExternalServiceError
from app.schemas.memory import ObservationCreate, TagCreate
from app.services import memory_service
from app.workflows import impression as impression_mod

JUDGEMENT = {"content": "写作类任务容易拖到最后一晚", "confidence": 0.6}
REVISION = {"content": "没人催的写作任务容易拖到最后一晚", "confidence": 0.5}

REVISE_MARK = "修订一条判断"  # impression_revise.md 的第一句，用来分辨两个阶段


def _tag(name: str = "写作") -> dict:
    return memory_service.create_tag(TagCreate(name=name, dimension="pattern"))


def _observation(content: str, tag_id: int) -> dict:
    return memory_service.create_observation(
        ObservationCreate(
            content=content,
            source_text=f"我{content}",
            tag_ids=[tag_id],
        )
    )


def _stub(monkeypatch, *, judgement=None, revision=None) -> list[dict]:
    """按 system prompt 分派返回值 —— 归纳和修订是两个不同的提示词。"""
    calls: list[dict] = []

    class StubProvider:
        def chat(self, messages, temperature=None, json_mode=False):
            system = messages[0]["content"]
            calls.append({"task": "impression", "system": system, "messages": messages})
            payload = revision if REVISE_MARK in system else judgement
            if payload is None:
                raise ExternalServiceError("这个阶段没有准备返回值")
            return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(impression_mod.factory, "get_provider", lambda task: StubProvider())
    return calls


def _stale_impression(monkeypatch, counterexamples: int = 3) -> dict:
    """建一条判断，再给它攒够反例。"""
    tag = _tag()
    for index in range(3):
        _observation(f"第 {index} 次说要做却没动", tag["id"])

    _stub(monkeypatch, judgement=JUDGEMENT)
    impression_mod.generate_pending()
    created = memory_service.list_impressions(include_superseded=False)[0]

    for index in range(counterexamples):
        counter = _observation(f"第 {index} 次反而提前交了", tag["id"])
        memory_service.record_counterexample(created["id"], counter["id"])

    return created


# ---------- 证据要带方向 ----------


def test_evidence_carries_its_relation(client, monkeypatch):
    """R6 加的 `relation` 字段要真的能读到 —— 修订靠它区分支持和反对。"""
    created = _stale_impression(monkeypatch)

    evidence = memory_service.list_impressions(include_superseded=False)[0]["evidence"]

    relations = {item["relation"] for item in evidence}
    assert relations == {"support", "counter"}
    assert len([item for item in evidence if item["relation"] == "counter"]) == 3


# ---------- 改写 ----------


def test_stale_impressions_are_rewritten(client, monkeypatch):
    created = _stale_impression(monkeypatch)
    _stub(monkeypatch, revision=REVISION)

    revised = impression_mod.revise_stale()

    assert len(revised) == 1
    assert revised[0]["content"] == REVISION["content"]
    assert revised[0]["generation"] == created["generation"] + 1
    assert revised[0]["counterexamples"] == 0  # 新一代从零开始数反例
    assert revised[0]["needs_revision"] is False


def test_the_old_generation_is_kept_as_superseded(client, monkeypatch):
    """旧版本永久保留 —— "他改过口"本身就是演化史的一部分（§7.3）。"""
    created = _stale_impression(monkeypatch)
    _stub(monkeypatch, revision=REVISION)

    impression_mod.revise_stale()

    all_versions = memory_service.list_impressions(include_superseded=True)
    assert len(all_versions) == 2

    old = next(v for v in all_versions if v["id"] == created["id"])
    assert old["status"] == "superseded"


def test_the_revision_prompt_carries_both_sides(client, monkeypatch):
    """★ 反例必须**被吸收进**新判断 —— 所以提示词里必须看得见它们。"""
    _stale_impression(monkeypatch)
    calls = _stub(monkeypatch, revision=REVISION)

    impression_mod.revise_stale()

    revise_call = next(c for c in calls if REVISE_MARK in c["system"])
    user_text = revise_call["messages"][-1]["content"]

    assert "支持它的事件（3 条）" in user_text
    assert "反对它的事件（3 条）" in user_text
    assert "反而提前交了" in user_text
    assert "说要做却没动" in user_text


def test_the_new_generation_keeps_all_the_evidence(client, monkeypatch):
    """★ 反例也要跟着进新一代 —— 它们已经被**吸收进**新判断了。

    ⚠️ 证据数必须在**加完反例之后**重新数：`_stale_impression()` 返回的是
    加反例**之前**那一份快照（我第一版就是拿它数的，所以数成了 3）。
    """
    _stale_impression(monkeypatch)
    before = len(
        memory_service.list_impressions(include_superseded=False)[0]["evidence"]
    )
    _stub(monkeypatch, revision=REVISION)

    revised = impression_mod.revise_stale()

    assert before == 6  # 3 条支持 + 3 条反例
    assert len(revised[0]["evidence"]) == before


# ---------- 不该改的 ----------


def test_a_healthy_impression_is_not_touched(client, monkeypatch):
    tag = _tag()
    for index in range(3):
        _observation(f"第 {index} 次说要做却没动", tag["id"])
    _stub(monkeypatch, judgement=JUDGEMENT)
    impression_mod.generate_pending()

    calls = _stub(monkeypatch, revision=REVISION)

    assert impression_mod.revise_stale() == []
    assert calls == []


def test_two_counterexamples_are_not_enough(client, monkeypatch):
    _stale_impression(monkeypatch, counterexamples=2)
    calls = _stub(monkeypatch, revision=REVISION)

    assert impression_mod.revise_stale() == []
    assert calls == []


# ---------- 幂等 / 重试 ----------


def test_revision_is_idempotent(client, monkeypatch):
    """跑成功了就不会再跑 —— 否则每一轮 tick 都会改出新的一代。"""
    _stale_impression(monkeypatch)
    _stub(monkeypatch, revision=REVISION)

    first = impression_mod.revise_stale()
    second = impression_mod.revise_stale()

    assert len(first) == 1
    assert second == []
    assert len(memory_service.list_impressions(include_superseded=True)) == 2


def test_a_failed_revision_is_retried_next_time(client, monkeypatch):
    created = _stale_impression(monkeypatch)
    _stub(monkeypatch, judgement=JUDGEMENT)  # 没准备修订的返回值 → 会抛

    counts = impression_mod.generate_pending()

    assert counts["revised"] == 0
    still = memory_service.list_impressions(include_superseded=False)[0]
    assert still["id"] == created["id"]
    assert still["needs_revision"] is True  # 还欠着，下一轮接着来

    _stub(monkeypatch, revision=REVISION)
    counts = impression_mod.generate_pending()

    assert counts["revised"] == 1


def test_generate_pending_reports_revisions(client, monkeypatch):
    _stale_impression(monkeypatch)
    _stub(monkeypatch, revision=REVISION)

    counts = impression_mod.generate_pending()

    assert counts["revised"] == 1
    assert set(counts) == {"created", "supported", "countered", "revised"}


# ---------- 静态守提示词 ----------


def test_the_prompt_forbids_explaining_the_counterexamples_away():
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("impression_revise.md")

    assert "反例必须被吸收进去" in prompt
    assert "把判断收窄" in prompt
    # 也要防止走向另一个极端：一有反例就把判断作废
    assert "不要" in prompt and "作废" in prompt
    assert "具体、不定性" in prompt
