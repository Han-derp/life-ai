"""聊天记录的编辑操作：删除 / 编辑 / 重新生成 / 回滚。

最重要的一条是 **`删聊天记录 ≠ 让他忘掉`**：

抽取时原话被**复制**进了 L1，所以删掉消息之后，他记住的事实
和那条事实的出处**都还在**。这不是漏洞（否则记忆会退化成
"没有出处的断言"，违反不变量 5），但它必须**被说出来** ——
所以这些操作都要能先问影响范围。
"""

import json

import pytest

from app.core.exceptions import (
    DomainValidationError,
    ExternalServiceError,
    NotFoundError,
)
from app.database.connection import get_connection
from app.services import chat_service, memory_service
from app.workflows import chat_history, extraction

QUOTE = "我今天背了两小时单词"


class _CaptureProvider:
    available = True
    last_usage = None

    def __init__(self, text: str = "新回复"):
        self.text = text
        self.fail = False
        self.seen: list[list[dict]] = []

    def chat(self, messages, temperature=None, json_mode=False):
        self.seen.append(messages)
        if self.fail:
            raise ExternalServiceError("模型不可用")
        return self.text


def _turn(content: str = QUOTE) -> dict:
    """直接落一条用户消息 + 一条回复（不走模型）。"""
    user = chat_service._save_message("user", content)
    assistant = chat_service._save_message("assistant", f"回复：{content}")
    return {"user": user, "assistant": assistant}


def _extract(monkeypatch, content: str = "背了两小时单词") -> dict:
    """把**最后一条**待抽取的用户消息抽成一条事实。

    `source_text` 直接取那条消息的原文 —— 抽取会拿它回原文核对，
    编一段不存在的引用会被丢掉（不变量 5），所以这里不能写死。
    """
    quote = extraction.pending_messages()[-1]["content"]

    class StubProvider:
        model = "test-analysis-model"

        def chat(self, messages, temperature=None, json_mode=False):
            return json.dumps(
                {
                    "observations": [
                        {"content": content, "source_text": quote, "confidence": 0.9}
                    ],
                    "suggested_tags": [],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(extraction.factory, "get_provider", lambda task: StubProvider())
    extraction.extract_pending()
    return memory_service.list_observations()[-1]


# ---------- 设计决定：删聊天 ≠ 让他忘掉 ----------


def test_deleting_a_message_keeps_the_memory(client, monkeypatch):
    """★ 这条是整个功能的语义核心。"""
    turn = _turn()
    observation = _extract(monkeypatch)

    chat_history.delete_message(turn["user"]["id"])

    # 事实还在
    assert any(o["id"] == observation["id"] for o in memory_service.list_observations())
    # **出处也还在**（raw_entries 没被级联删掉，只是断了指向消息的链接）
    with get_connection() as conn:
        entry = conn.execute(
            "SELECT * FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()

    assert entry is not None
    assert entry["content"] == QUOTE
    assert entry["message_id"] is None


def test_the_impact_lists_the_derived_memories(client, monkeypatch):
    turn = _turn()
    observation = _extract(monkeypatch)

    impact = chat_history.impact_of_deleting(turn["user"]["id"])

    assert [item["id"] for item in impact["affected_observations"]] == [observation["id"]]


def test_deleting_with_memory_removes_the_derived_observations(client, monkeypatch):
    turn = _turn()
    _extract(monkeypatch)

    result = chat_history.delete_message(turn["user"]["id"], with_memory=True)

    assert result["deleted_observations"] == 1
    assert memory_service.list_observations() == []


def test_the_impact_can_no_longer_be_computed_after_the_message_is_gone(client, monkeypatch):
    """``message_id`` 是 ``ON DELETE SET NULL`` —— 消息没了，链接就断了。

    所以**必须先算影响再删**（`_delete_derived()` 在 `delete_message()` 之前调用）。
    删完之后再问影响范围会得到 404 —— 这是对的：那条消息已经不存在了。
    """
    turn = _turn()
    _extract(monkeypatch)

    chat_history.delete_message(turn["user"]["id"])

    with pytest.raises(NotFoundError):
        chat_history.impact_of_deleting(turn["user"]["id"])


def test_a_refused_deletion_is_reported(client, monkeypatch):
    """不变量 6：这是某条判断的**最后一条依据**时，删除必须被拒绝并如实上报。"""
    turn = _turn()
    observation = _extract(monkeypatch)
    memory_service.create_impression("写作容易拖", [observation["id"]], confidence=0.6)

    result = chat_history.delete_message(turn["user"]["id"], with_memory=True)

    assert result["deleted_observations"] == 0
    assert len(result["refused"]) == 1
    # 事实和判断都还在
    assert len(memory_service.list_observations()) == 1
    assert len(memory_service.list_impressions(include_superseded=False)) == 1


# ---------- 编辑：不动 L1 ----------


def test_editing_a_message_does_not_touch_l1(client, monkeypatch):
    """★ 改的是聊天记录。L1 里那份"你当时真的说过的话"**不改写**。"""
    turn = _turn()
    observation = _extract(monkeypatch)

    result = chat_history.edit_message(turn["user"]["id"], "我今天背了三小时单词")

    assert result["message"]["content"] == "我今天背了三小时单词"
    assert "不改写历史" in result["note"]

    with get_connection() as conn:
        entry = conn.execute(
            "SELECT content FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()

    assert entry["content"] == QUOTE  # 原话没变


def test_editing_the_assistant_reply_is_refused(client):
    turn = _turn()

    with pytest.raises(DomainValidationError):
        chat_history.edit_message(turn["assistant"]["id"], "我替你改一句")


def test_editing_to_empty_is_refused(client):
    turn = _turn()

    with pytest.raises(DomainValidationError):
        chat_history.edit_message(turn["user"]["id"], "   ")


# ---------- 重新生成 ----------


def test_regenerate_replaces_the_old_reply(client, monkeypatch):
    turn = _turn()
    provider = _CaptureProvider("第二条回复")
    monkeypatch.setattr(chat_service, "_provider", provider)

    new_message = chat_service.regenerate_reply(turn["assistant"]["id"])

    assert new_message["content"] == "第二条回复"
    # 旧的那条被替换掉了（不是留成两份）
    ids = [m["id"] for m in chat_service.list_messages()]
    assert turn["assistant"]["id"] not in ids


def test_regenerate_uses_the_same_context_shape_as_send_chat(client, monkeypatch):
    """两条路共用 `_generate_reply` —— 所以形状必须一致。"""
    turn = _turn()
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", provider)

    chat_service.regenerate_reply(turn["assistant"]["id"])

    messages = provider.seen[-1]
    assert messages[0]["role"] == "system"
    assert "行动搭档" in messages[0]["content"]  # 人格块在
    assert messages[-1]["role"] == "user"
    # 用户那句话带着「现在几点」
    assert QUOTE in messages[-1]["content"]


def test_regenerate_keeps_the_old_reply_when_generation_fails(client, monkeypatch):
    """★ 先生成、成功了再删旧的 —— 否则一次失败会变成两次损失。"""
    turn = _turn()
    provider = _CaptureProvider()
    provider.fail = True
    monkeypatch.setattr(chat_service, "_provider", provider)

    with pytest.raises(ExternalServiceError):
        chat_service.regenerate_reply(turn["assistant"]["id"])

    ids = [m["id"] for m in chat_service.list_messages()]
    assert turn["assistant"]["id"] in ids  # 原来那条还在


# ---------- 回滚 ----------


def test_rollback_deletes_everything_after(client, monkeypatch):
    first = _turn("第一句")
    _turn("第二句")
    _turn("第三句")

    result = chat_history.rollback(first["user"]["id"])

    assert result["deleted_messages"] == 5  # 第一句之后的全部（含它自己的回复）
    assert [m["content"] for m in chat_service.list_messages()] == ["第一句"]


def test_rollback_keeps_the_memory_by_default(client, monkeypatch):
    """★ 回滚只动聊天记录 —— 他不会因此忘掉那段时间。"""
    first = _turn("第一句")
    _turn("第二句")
    observation = _extract(monkeypatch, content="背了单词")

    chat_history.rollback(first["user"]["id"])

    assert any(o["id"] == observation["id"] for o in memory_service.list_observations())


def test_rollback_with_memory_removes_the_derived_observations(client, monkeypatch):
    first = _turn("第一句")
    _turn("第二句")
    _extract(monkeypatch)

    result = chat_history.rollback(first["user"]["id"], with_memory=True)

    assert result["deleted_observations"] == 1
    assert memory_service.list_observations() == []


def test_rollback_impact_preview(client, monkeypatch):
    first = _turn("第一句")
    _turn("第二句")
    observation = _extract(monkeypatch)

    impact = chat_history.impact_of_rollback(first["user"]["id"])

    assert [item["id"] for item in impact["affected_observations"]] == [observation["id"]]


# ---------- API ----------


def test_api_delete_and_impact(client, monkeypatch):
    turn = _turn()
    _extract(monkeypatch)

    impact = client.get(
        f"/api/v1/chat/messages/{turn['user']['id']}/impact"
    ).json()
    assert len(impact["affected_observations"]) == 1

    body = client.delete(f"/api/v1/chat/messages/{turn['user']['id']}").json()
    assert body["deleted_messages"] == 1
    assert body["deleted_observations"] == 0  # 默认不删记忆


def test_api_edit_and_regenerate(client, monkeypatch):
    turn = _turn()
    provider = _CaptureProvider("重来的回复")
    monkeypatch.setattr(chat_service, "_provider", provider)

    edited = client.patch(
        f"/api/v1/chat/messages/{turn['user']['id']}",
        json={"content": "改过的话"},
    ).json()
    assert edited["message"]["content"] == "改过的话"

    regenerated = client.post(
        f"/api/v1/chat/messages/{turn['assistant']['id']}/regenerate"
    ).json()
    assert regenerated["content"] == "重来的回复"


def test_api_rollback(client, monkeypatch):
    first = _turn("第一句")
    _turn("第二句")

    body = client.post(f"/api/v1/chat/rollback/{first['user']['id']}").json()

    assert body["deleted_messages"] == 3
    assert len(client.get("/api/v1/chat/messages").json()) == 1


# ---------- 编辑 + 让他按新版本重记 ----------


def test_editing_with_memory_deletes_the_old_facts(client, monkeypatch):
    turn = _turn()
    _extract(monkeypatch)

    result = chat_history.edit_message(
        turn["user"]["id"], "我今天背了三小时单词", with_memory=True
    )

    assert result["deleted_observations"] == 1
    assert memory_service.list_observations() == []
    assert "重新抽取" in result["note"]


def test_editing_with_memory_marks_the_message_for_re_extraction(client, monkeypatch):
    """★ 机制：不在这里同步重抽，只标回"待抽取"，交给调度器。

    模型调用可能耗时，同步重抽会把编辑请求挂住。
    """
    turn = _turn()
    _extract(monkeypatch)
    assert extraction.pending_messages() == []

    chat_history.edit_message(turn["user"]["id"], "背了三小时", with_memory=True)

    pending = extraction.pending_messages()
    assert [item["id"] for item in pending] == [turn["user"]["id"]]


def test_editing_without_memory_does_not_trigger_re_extraction(client, monkeypatch):
    """默认路径**不能**造成"改个字就被重抽一遍"的意外行为。"""
    turn = _turn()
    _extract(monkeypatch)

    chat_history.edit_message(turn["user"]["id"], "背了三小时")

    assert extraction.pending_messages() == []


def test_the_old_source_text_stays_in_l1(client, monkeypatch):
    """★ L1 只追加：档案里同时留下旧版和新版。

    档案记录的是"你先后说过什么"，不是"你最后认可什么"。
    """
    turn = _turn()
    observation = _extract(monkeypatch)
    old_entry_id = observation["raw_entry_id"]

    chat_history.edit_message(turn["user"]["id"], "背了三小时", with_memory=True)

    with get_connection() as conn:
        entry = conn.execute(
            "SELECT content FROM raw_entries WHERE id = ?",
            (old_entry_id,),
        ).fetchone()

    assert entry is not None
    assert entry["content"] == QUOTE


def test_re_extraction_picks_up_the_new_version(client, monkeypatch):
    """★★ 完整闭环：编辑 → 重抽 → 事实按**新版本**记。"""
    turn = _turn()
    _extract(monkeypatch)  # 旧事实：背了两小时单词（摘的是原文 QUOTE）

    chat_history.edit_message(
        turn["user"]["id"], "我今天背了三小时单词", with_memory=True
    )
    # 调度器接手：这时待抽取的那条消息已经是新版正文
    _extract(monkeypatch, content="背了三小时单词")

    observations = memory_service.list_observations()
    assert len(observations) == 1
    assert observations[0]["content"] == "背了三小时单词"
    assert observations[0]["source_text"] == "我今天背了三小时单词"


def test_editing_an_unextracted_message_with_memory_is_fine(client):
    """没有衍生事实时选"一起处理"不该出错。"""
    turn = _turn()

    result = chat_history.edit_message(
        turn["user"]["id"], "改一句没被抽过的话", with_memory=True
    )

    assert result["deleted_observations"] == 0
    assert result["refused"] == []


def test_api_edit_with_memory(client, monkeypatch):
    turn = _turn()
    _extract(monkeypatch)

    body = client.patch(
        f"/api/v1/chat/messages/{turn['user']['id']}?with_memory=true",
        json={"content": "我今天背了三小时单词"},
    ).json()

    assert body["deleted_observations"] == 1
    assert body["message"]["content"] == "我今天背了三小时单词"


# ---------- 连档案一起抹掉 ----------


def test_editing_with_purge_erases_the_old_source_text(client, monkeypatch):
    turn = _turn()
    observation = _extract(monkeypatch)

    result = chat_history.edit_message(
        turn["user"]["id"],
        "背了三小时",
        with_memory=True,
        purge_archive=True,
    )

    assert result["purged_raw_entries"] == 1
    with get_connection() as conn:
        gone = conn.execute(
            "SELECT 1 FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()
    assert gone is None


def test_deleting_with_purge_erases_the_old_source_text(client, monkeypatch):
    """删除和回滚也有同一个选项（三条路一致）。"""
    turn = _turn()
    observation = _extract(monkeypatch)

    result = chat_history.delete_message(
        turn["user"]["id"], with_memory=True, purge_archive=True
    )

    assert result["purged_raw_entries"] == 1
    with get_connection() as conn:
        gone = conn.execute(
            "SELECT 1 FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()
    assert gone is None


def test_a_referenced_raw_entry_is_never_purged(client, monkeypatch):
    """★ 安全阀：只要那条事实还在（删不掉），它的出处**必须**留着。

    有引用还抹，那条事实就会引用一句不存在的话 —— 不变量 5 当场破掉。
    """
    turn = _turn()
    observation = _extract(monkeypatch)
    memory_service.create_impression("写作容易拖", [observation["id"]], confidence=0.6)

    result = chat_history.edit_message(
        turn["user"]["id"],
        "背了三小时",
        with_memory=True,
        purge_archive=True,
    )

    assert result["deleted_observations"] == 0
    assert result["refused"]
    assert result["purged_raw_entries"] == 0

    with get_connection() as conn:
        entry = conn.execute(
            "SELECT content FROM raw_entries WHERE id = ?",
            (observation["raw_entry_id"],),
        ).fetchone()
    assert entry is not None
    assert entry["content"] == QUOTE  # 出处原样保留


def test_the_service_guard_itself(client, monkeypatch):
    """直接测安全阀 —— 不依赖上层怎么调它。"""
    _turn()
    observation = _extract(monkeypatch)
    entry_id = observation["raw_entry_id"]

    assert memory_service.delete_raw_entry_if_unreferenced(entry_id) is False

    memory_service.delete_observation(observation["id"])

    assert memory_service.delete_raw_entry_if_unreferenced(entry_id) is True


def test_api_edit_with_purge_archive(client, monkeypatch):
    turn = _turn()
    observation = _extract(monkeypatch)

    body = client.patch(
        f"/api/v1/chat/messages/{turn['user']['id']}"
        "?with_memory=true&purge_archive=true",
        json={"content": "背了三小时"},
    ).json()

    assert body["deleted_observations"] == 1
    assert body["purged_raw_entries"] == 1
    assert "档案" in body["note"]
