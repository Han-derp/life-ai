"""抽取挂标签（设计裁决 R7）+ 标签建议（R8）。

三条要守住的：

1. **只能从清单里挑。** 她不能自己创建标签 —— 那是用户的分类权（§6.4）。
   清单外的 id（模型编的）一律丢掉。
2. **匹配不上就不挂。** 裁决原话："宁可不挂，也不要挂错" ——
   硬塞会污染标签体系，而标签是印象系统的检索基础。
3. **建议的三条硬约束**：同 (name, dimension) 只累加 count；
   `rejected` 永不再建议；批准后写入 tags 并清理建议。
"""

import json

from app.schemas.memory import ObservationCreate, TagCreate
from app.services import memory_service, tag_suggestion_service
from app.workflows import extraction

QUOTE = "今天背了两小时单词"


def _user_message(content: str = QUOTE) -> int:
    from app.core.time_utils import to_utc_iso, utc_now
    from app.database.connection import get_connection

    with get_connection() as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
        ).fetchone()["id"]
        cursor = conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, model, metadata_json, created_at
            ) VALUES (?, 'user', ?, NULL, NULL, ?)
            """,
            (conversation_id, content, to_utc_iso(utc_now())),
        )
        return cursor.lastrowid


def _stub(monkeypatch, payload) -> list[dict]:
    calls: list[dict] = []

    class StubProvider:
        model = "test-analysis-model"

        def chat(self, messages, temperature=None, json_mode=False):
            calls.append({"task": "extract", "messages": messages, "json_mode": json_mode})
            return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(extraction.factory, "get_provider", lambda task: StubProvider())
    return calls


def _observation_payload(tag_ids: list[int] | None = None, suggestions=None) -> dict:
    observation = {
        "content": "背了两小时单词",
        "source_text": QUOTE,
        "confidence": 0.9,
    }
    if tag_ids is not None:
        observation["tag_ids"] = tag_ids
    return {
        "observations": [observation],
        "suggested_tags": suggestions or [],
    }


# ---------- 冷启动：预置标签 ----------


def test_the_default_tags_are_seeded(client):
    """没有它，全新安装永远凑不出"同类"、归纳永远不触发（裁决 R7）。"""
    dimensions = {tag["dimension"] for tag in memory_service.list_tags()}

    assert dimensions == {"state", "domain", "object", "pattern", "time"}


# ---------- 只从清单里挑 ----------


def test_the_batch_carries_the_tag_vocabulary(client, monkeypatch):
    tag = memory_service.create_tag(TagCreate(name="写作", dimension="pattern"))
    _user_message()
    calls = _stub(monkeypatch, _observation_payload())

    extraction.extract_pending()

    batch = calls[0]["messages"][-1]["content"]
    assert f"[{tag['id']}] 写作（pattern）" in batch


def test_observations_get_tags_from_the_vocabulary(client, monkeypatch):
    tag = memory_service.create_tag(TagCreate(name="写作", dimension="pattern"))
    _user_message()
    _stub(monkeypatch, _observation_payload([tag["id"]]))

    extraction.extract_pending()

    saved = memory_service.list_observations()
    assert [t["name"] for t in saved[-1]["tags"]] == ["写作"]


def test_tag_ids_outside_the_vocabulary_are_dropped(client, monkeypatch):
    """**不信任模型传回来的编号** —— 它可能编一个不存在的 tag_id。"""
    _user_message()
    _stub(monkeypatch, _observation_payload([9999]))

    extraction.extract_pending()

    assert memory_service.list_observations()[-1]["tags"] == []


def test_no_tag_is_better_than_a_wrong_one(client, monkeypatch):
    """裁决 R7：匹配不上就不挂 —— 不许硬塞一个"最接近"的。"""
    memory_service.create_tag(TagCreate(name="写作", dimension="pattern"))
    _user_message()
    _stub(monkeypatch, _observation_payload([]))

    extraction.extract_pending()

    assert memory_service.list_observations()[-1]["tags"] == []


# ---------- 建议（R8） ----------


def test_unmatched_observations_produce_a_suggestion(client, monkeypatch):
    _user_message()
    _stub(
        monkeypatch,
        _observation_payload(
            [],
            [{"name": "通宵", "dimension": "pattern", "reason": "反复提到睡得很晚"}],
        ),
    )

    result = extraction.extract_pending()

    assert result["suggested_tags"] == 1
    pending = tag_suggestion_service.list_pending()
    assert pending[0]["name"] == "通宵"
    assert pending[0]["count"] == 1
    # 可追溯（R8 的 source_observation_id）
    assert pending[0]["source_observation_id"] is not None


def test_a_repeated_suggestion_only_bumps_the_count(client, monkeypatch):
    """约束 1：只累加 count，不新增行 —— 否则你会看到 20 条"想睡觉"，无从下手。"""
    _user_message()
    payload = _observation_payload(
        [], [{"name": "通宵", "dimension": "pattern", "reason": "又提到了"}]
    )
    _stub(monkeypatch, payload)
    extraction.extract_pending()

    _user_message("又熬到三点")
    _stub(monkeypatch, payload)
    extraction.extract_pending()

    pending = tag_suggestion_service.list_pending()
    assert len(pending) == 1
    assert pending[0]["count"] == 2


def test_a_rejected_tag_is_never_suggested_again(client, monkeypatch):
    """约束 2 —— 这是关系里最烦人的行为之一：反复提你拒绝过的东西。"""
    _user_message()
    _stub(
        monkeypatch,
        _observation_payload([], [{"name": "通宵", "dimension": "pattern"}]),
    )
    extraction.extract_pending()

    suggestion = tag_suggestion_service.list_pending()[0]
    tag_suggestion_service.reject(suggestion["id"])
    assert tag_suggestion_service.list_pending() == []

    # 再抽一批、她再提一次 —— 必须被拦住
    _user_message("又熬到三点")
    _stub(
        monkeypatch,
        _observation_payload([], [{"name": "通宵", "dimension": "pattern"}]),
    )
    extraction.extract_pending()

    assert tag_suggestion_service.list_pending() == []


def test_approving_writes_the_tag_and_cleans_the_suggestion(client, monkeypatch):
    """约束 3：避免"已批准但还挂在待办里"。"""
    _user_message()
    _stub(
        monkeypatch,
        _observation_payload([], [{"name": "通宵", "dimension": "pattern"}]),
    )
    extraction.extract_pending()

    suggestion = tag_suggestion_service.list_pending()[0]
    result = tag_suggestion_service.approve(suggestion["id"])

    assert result["tag"]["name"] == "通宵"
    assert tag_suggestion_service.list_pending() == []
    assert any(tag["name"] == "通宵" for tag in memory_service.list_tags())


def test_a_tag_that_already_exists_is_not_suggested(client, monkeypatch):
    memory_service.create_tag(TagCreate(name="通宵", dimension="pattern"))
    _user_message()
    _stub(
        monkeypatch,
        _observation_payload([], [{"name": "通宵", "dimension": "pattern"}]),
    )

    extraction.extract_pending()

    assert tag_suggestion_service.list_pending() == []


def test_a_bad_dimension_is_ignored(client, monkeypatch):
    """维度只允许那五个（和 tags 表的 CHECK 对齐）。"""
    _user_message()
    _stub(monkeypatch, _observation_payload([], [{"name": "通宵", "dimension": "作息"}]))

    extraction.extract_pending()

    assert tag_suggestion_service.list_pending() == []


# ---------- 静态守提示词 ----------


def test_the_prompt_forbids_inventing_tags():
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("extract.md")

    assert "不许自己发明新标签" in prompt
    assert "宁可不挂" in prompt
    assert "suggested_tags" in prompt
