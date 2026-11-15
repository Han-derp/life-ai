"""「行动计划」—— 三层计划（完整 / **最低** / **备用** ✓）。

## 设计要点（2026-10-05 用户拍板 ✓）

- **AI 出草稿** ✓ → **用户确认才入库** ✓（不变量 2：AI 只建议 ✓）
- **不注入人格** ✓ —— 这是**工具性**的功能 ✓，不是"她"
- **AI 失败要给保底模板，但必须标明 `source="template"`** ✗
  —— 悄悄冒充 AI 的方案**不可接受** ✓
"""

import json

import pytest

from app.core.exceptions import DomainValidationError, ExternalServiceError
from app.database.connection import get_connection
from app.llm import factory
from app.schemas.action_plan import ActionPlanCreate, ActionStep
from app.services import action_plan_ai_service, action_plan_service


def _plan(**overrides) -> ActionPlanCreate:
    data = {
        "title": "把考试复习开个头",
        "reason": "只剩三周，先做能开始的",
        "minimum_plan": "只看一页",
        "fallback_plan": "把书打开放桌上",
        "steps": [ActionStep(title="翻到第一章", minutes=15, priority="high")],
    }
    data.update(overrides)
    return ActionPlanCreate(**data)


class _FakeProvider:
    name = "fake"

    def __init__(self, payload: str = "") -> None:
        self.payload = payload

    def chat(self, messages, temperature=None, json_mode=False):
        return self.payload


def test_create_starts_as_draft(client):
    plan = action_plan_service.create_plan(_plan())

    assert plan["status"] == "draft"
    assert plan["steps"][0]["title"] == "翻到第一章"


def test_confirm_moves_it_to_confirmed(client):
    plan = action_plan_service.create_plan(_plan())

    assert action_plan_service.confirm_plan(plan["id"])["status"] == "confirmed"


def test_confirming_twice_is_rejected(client):
    """★ 确认过的不能再确认 ✓（只有 `draft` / `adjusted` 能确认 ✓）。"""
    plan = action_plan_service.create_plan(_plan())
    action_plan_service.confirm_plan(plan["id"])

    with pytest.raises(DomainValidationError):
        action_plan_service.confirm_plan(plan["id"])


def test_list_filters_by_status(client):
    draft = action_plan_service.create_plan(_plan())
    done = action_plan_service.create_plan(_plan(title="另一件"))
    action_plan_service.confirm_plan(done["id"])

    drafts = action_plan_service.list_plans(status="draft")

    assert [item["id"] for item in drafts] == [draft["id"]]


def test_a_broken_steps_json_does_not_crash_the_list(client):
    """★ 一行坏数据不该让整个列表 500 ✗。"""
    plan = action_plan_service.create_plan(_plan())
    with get_connection() as conn:
        conn.execute(
            "UPDATE action_plans SET steps_json = ? WHERE id = ?",
            ("{不是JSON", plan["id"]),
        )

    listed = action_plan_service.list_plans()

    assert listed[0]["steps"] == []


def test_the_api_exposes_create_list_and_confirm(client):
    created = client.post(
        "/api/v1/action-plans",
        json={
            "title": "开个头",
            "steps": [{"title": "翻到第一章", "minutes": 15, "priority": "high"}],
        },
    )
    assert created.status_code == 201
    plan_id = created.json()["id"]

    assert client.get("/api/v1/action-plans?status=draft").json()[0]["id"] == plan_id
    confirmed = client.post(f"/api/v1/action-plans/{plan_id}/confirm")
    assert confirmed.json()["status"] == "confirmed"


def test_the_ai_draft_is_marked_as_ai(client, monkeypatch):
    """★ AI 成功时 `source == "ai"` ✓。"""
    monkeypatch.setattr(
        factory,
        "get_provider",
        lambda task: _FakeProvider(
            json.dumps(
                {
                    "title": "开个头",
                    "reason": "先做能开始的",
                    "minimum_plan": "只看一页",
                    "fallback_plan": "打开书",
                    "steps": [
                        {"title": "翻到第一章", "minutes": 15, "priority": "high"}
                    ],
                }
            )
        ),
    )

    draft = action_plan_ai_service.generate_draft("我要复习考试")

    assert draft.source == "ai"
    assert draft.title == "开个头"
    assert draft.steps[0].minutes == 15


def test_a_failed_ai_draft_falls_back_but_says_so(client, monkeypatch):
    """★★ **这条最重要** ✗ —— AI 挂了给模板可以 ✓，但**必须标明是模板** ✓。"""

    class _Broken(_FakeProvider):
        def chat(self, messages, temperature=None, json_mode=False):
            raise ExternalServiceError("挂了")

    monkeypatch.setattr(factory, "get_provider", lambda task: _Broken())

    draft = action_plan_ai_service.generate_draft("我要复习考试")

    assert draft.source == "template"
    assert "保底模板" in (draft.reason or "")


def test_garbage_from_the_model_also_falls_back(client, monkeypatch):
    """模型返回的不是 JSON ✓（或者格式不对 ✓）→ 也走模板 ✓，不抛 500 ✗。"""
    monkeypatch.setattr(
        factory, "get_provider", lambda task: _FakeProvider("我觉得你应该努力")
    )

    draft = action_plan_ai_service.generate_draft("做点事")

    assert draft.source == "template"


def test_the_draft_endpoint_does_not_save_anything(client, monkeypatch):
    """★ 草稿**不入库** ✓ —— 用户确认了才存 ✓（不变量 2 ✓）。"""
    monkeypatch.setattr(
        factory,
        "get_provider",
        lambda task: _FakeProvider(
            json.dumps(
                {
                    "title": "x",
                    "minimum_plan": "y",
                    "fallback_plan": "z",
                    "steps": [{"title": "一步", "minutes": 5, "priority": "low"}],
                }
            )
        ),
    )

    response = client.post("/api/v1/action-plans/draft", json={"message": "做点事"})

    assert response.status_code == 200
    assert response.json()["source"] == "ai"
    assert action_plan_service.list_plans() == []


# ---------- 「卡在哪」（blocking_type）✓ 2026-10-05 加 ----------


def test_the_draft_reports_what_blocks_him(client, monkeypatch):
    """★ `blocking_type` 是界面那个状态徽章的数据源 ✓（`ACTION_CENTER_MVP.md` ✓）。"""
    monkeypatch.setattr(
        factory,
        "get_provider",
        lambda task: _FakeProvider(
            json.dumps(
                {
                    "blocking_type": "time_pressure",
                    "title": "今晚把报告开个头",
                    "reason": "明天截止，你现在只剩两小时",
                    "minimum_plan": "只整理数据",
                    "fallback_plan": "打开文档写三个小标题",
                    "steps": [
                        {"title": "整理实验数据", "minutes": 25, "priority": "high"}
                    ],
                }
            )
        ),
    )

    draft = action_plan_ai_service.generate_draft("明天要交实验报告，但我很累")

    assert draft.blocking_type == "time_pressure"
    assert draft.reason


def test_a_template_fallback_does_not_invent_a_blocking_type(client, monkeypatch):
    """★ AI 没成功时**不许瞎猜** `blocking_type` ✗ —— 留空 ✓。"""

    class _Broken(_FakeProvider):
        def chat(self, messages, temperature=None, json_mode=False):
            raise ExternalServiceError("挂了")

    monkeypatch.setattr(factory, "get_provider", lambda task: _Broken())

    draft = action_plan_ai_service.generate_draft("我拖了三天")

    assert draft.blocking_type is None


def test_a_bogus_blocking_type_is_rejected(client, monkeypatch):
    """★ 模型返回七个之外的值 ✗ → 当作格式不对 ✓，走保底模板 ✓（不抛 500 ✗）。"""
    monkeypatch.setattr(
        factory,
        "get_provider",
        lambda task: _FakeProvider(
            json.dumps(
                {
                    "blocking_type": "心情不好",
                    "title": "x",
                    "steps": [{"title": "一步", "minutes": 5, "priority": "low"}],
                }
            )
        ),
    )

    assert action_plan_ai_service.generate_draft("做点事").source == "template"


def test_the_blocking_type_is_stored_with_the_plan(client):
    """★ 存进库 ✓ —— ⑥「临近截止就重排」将来要靠它 ✓。"""
    plan = action_plan_service.create_plan(_plan(blocking_type="stalled"))

    assert plan["blocking_type"] == "stalled"
