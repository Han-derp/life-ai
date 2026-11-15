"""S4 剩下的那块：**自动记忆抽取**。

要守住三件事：

1. **有出处才存在**（不变量 5）—— 而且**不信模型传的引用**，核不到就丢
2. **不重复抽** —— 抽过的消息不会被抽第二次
3. **模型调用失败不标记完成** —— 失败后消息仍然留在待抽取队列中。

每个用例都把模型调用显式 stub，避免依赖真实 API Key。
"""

import json
from datetime import datetime, timezone

from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.services import memory_service
from app.workflows import extraction

QUOTE = "今天背了两小时单词，有点累"


def _user_message(content: str, moment: datetime | None = None) -> int:
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
            (conversation_id, content, to_utc_iso(moment or utc_now())),
        )
        return cursor.lastrowid


def _stub(monkeypatch, payload, model: str = "test-analysis-model") -> list[dict]:
    """把抽取用的模型调用钉死，并记下它收到的参数。"""
    calls: list[dict] = []

    class StubProvider:
        name = "openai-compatible"
        available = True
        last_usage = None

        def chat(self, messages, temperature=None, json_mode=False):
            calls.append({"task": "extract", "messages": messages, "json_mode": json_mode})
            return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    StubProvider.model = model
    monkeypatch.setattr(extraction.factory, "get_provider", lambda task: StubProvider())
    return calls


def _payload(*quotes: str) -> dict:
    return {
        "observations": [
            {"content": f"观察 {index}", "source_text": quote, "confidence": 0.9}
            for index, quote in enumerate(quotes)
        ]
    }


# ---------- 功能 ----------


def test_extracts_an_observation_whose_source_is_verbatim(client, monkeypatch):
    _user_message(f"嗯……{QUOTE}")
    _stub(monkeypatch, _payload(QUOTE))

    result = extraction.extract_pending()

    assert result["created_observations"] == 1
    assert result["dropped_without_source"] == 0
    assert result["model"] == "test-analysis-model"

    saved = memory_service.list_observations()
    assert saved[-1]["content"] == "观察 0"
    assert saved[-1]["source_text"] == QUOTE


def test_json_mode_is_requested(client, monkeypatch):
    """抽取要的是严格 JSON —— 必须让 provider 走解码层强制。"""
    _user_message(QUOTE)
    calls = _stub(monkeypatch, _payload(QUOTE))

    extraction.extract_pending()

    assert calls[0]["json_mode"] is True
    assert calls[0]["task"] == "extract"


def test_nothing_to_do_does_not_call_the_model(client, monkeypatch):
    calls = _stub(monkeypatch, _payload(QUOTE))

    result = extraction.extract_pending()

    assert result["checked_messages"] == 0
    assert calls == []


# ---------- 不变量 5 的机械保证 ----------


def test_drops_observations_whose_source_is_not_in_the_original(client, monkeypatch):
    """**模型改写引用 → 整条丢掉。**

    这是不变量 5 落在代码里的样子：没有出处，她不许说"我记得"。
    提示词里写了"必须引用原文"，但提示词不是保证 —— 这里才是。
    """
    _user_message(QUOTE)
    _stub(monkeypatch, _payload("他背了两小时单词"))  # 改写了人称 → 原文里没有

    result = extraction.extract_pending()

    assert result["created_observations"] == 0
    assert result["dropped_without_source"] == 1
    assert memory_service.list_observations() == []


def test_drops_observations_with_no_source_at_all(client, monkeypatch):
    _user_message(QUOTE)
    _stub(monkeypatch, {"observations": [{"content": "背了单词", "confidence": 0.9}]})

    result = extraction.extract_pending()

    assert result["created_observations"] == 0
    assert result["dropped_without_source"] == 1


# ---------- 幂等 ----------


def test_does_not_extract_the_same_message_twice(client, monkeypatch):
    _user_message(QUOTE)
    _stub(monkeypatch, _payload(QUOTE))

    first = extraction.extract_pending()
    second = extraction.extract_pending()

    assert first["checked_messages"] == 1
    assert second["checked_messages"] == 0
    assert len(memory_service.list_observations()) == 1


def test_marks_messages_even_when_nothing_is_extractable(client, monkeypatch):
    """抽不出东西也算处理过 —— 否则寒暄会被无限重抽。"""
    _user_message("在吗")
    _stub(monkeypatch, {"observations": []})

    first = extraction.extract_pending()
    second = extraction.extract_pending()

    assert first["created_observations"] == 0
    assert second["checked_messages"] == 0


def test_malformed_json_is_survivable(client, monkeypatch):
    """模型返回的 JSON 不一定完全符合要求（`format: json` 也只是"强约束"）。"""
    _user_message(QUOTE)
    _stub(monkeypatch, "这根本不是 JSON")

    result = extraction.extract_pending()

    assert result["created_observations"] == 0
    assert result["dropped_without_source"] == 0  # 连解析都没过


# ---------- 时间归属 ----------


def test_observation_date_follows_the_message_not_now(client, monkeypatch):
    """一批**旧**消息不能被整批记成"今天" —— 否则历史日期全错。"""
    moment = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    _user_message(QUOTE, moment)
    _stub(monkeypatch, _payload(QUOTE))

    extraction.extract_pending()

    saved = memory_service.list_observations()
    assert saved[-1]["local_date"] == "2026-06-01"  # 当地 20:00，仍是 6/1


# ---------- 模型失败时可重试 ----------


def test_provider_failure_does_not_mark_messages_extracted(client, monkeypatch):
    from app.core.exceptions import ExternalServiceError

    _user_message(QUOTE)

    class BrokenProvider:
        model = "test-analysis-model"

        def chat(self, messages, temperature=None, json_mode=False):
            raise ExternalServiceError("模型服务暂不可用")

    monkeypatch.setattr(extraction.factory, "get_provider", lambda task: BrokenProvider())

    import pytest
    with pytest.raises(ExternalServiceError):
        extraction.extract_pending()

    assert len(extraction.pending_messages()) == 1


def test_the_prompt_forbids_inference():
    """**静态**守提示词：不靠"看模型今天心情"。

    抽取最危险的失败模式是**编造** —— 记忆库整个建立在"不许编造"上。
    """
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("extract.md")

    assert "只抽用户真正说过的" in prompt
    assert "源片段" in prompt or "原文片段" in prompt
    assert "宁缺毋滥" in prompt


# ---------- 相对时间 ----------


def test_the_batch_carries_each_message_local_time(client, monkeypatch):
    """模型必须知道那句话**是什么时候说的**，否则"昨天"没法换算。

    真实案例：用户说"我昨天把软件推进了一大半"，
    抽出来的 content 是"把给你的软件推进了一大半"，
    而 `observed_at` 是**消息时间**（10-04）—— 活其实是 10-03 干的。
    """
    moment = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)  # 当地 20:00
    _user_message(QUOTE, moment)
    calls = _stub(monkeypatch, _payload(QUOTE))

    extraction.extract_pending()

    batch = calls[0]["messages"][-1]["content"]
    assert "2026-06-01" in batch
    assert "20:00" in batch


def test_the_prompt_tells_the_model_to_resolve_relative_dates():
    """**静态**守提示词：'昨天'存进去，这条记忆过两天就没法读了。"""
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("extract.md")

    assert "相对时间" in prompt
    assert "绝对" in prompt
