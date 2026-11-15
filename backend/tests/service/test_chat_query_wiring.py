"""R-1：`stream_chat` 必须和 `_generate_reply` 走**同一条**上下文。

设计窗口 0.2 抓到的真 bug：`stream_chat`（**前端唯一走的那条路**）
漏传了 `query=clean`。连带后果两条：

1. **FTS5 相关度检索在真实聊天里一次都没被调用过** ——
   `query=None` 时 `_pick_impressions()/_pick_observations()` 直接走
   `list_*()`（按时间取最近），migration 011 那一整套等于没接上 ✗
2. **面板在撒谎** —— `build_context_plan()` 传了 query，
   所以面板上显示的那批记忆，和实际发出去的可能不是同一批 ✗。
   而 S2 面板的全部承诺就是"看到的就是要发的"。

为什么这条排在隐私裁决**之前**：一个不可复现的上下文，
做不出可靠的隐私开关 —— "我标记了它，它到底有没有被发出去"
要先能回答"到底发了什么"。
"""

from app.ai.context import assembler
from app.schemas.memory import ObservationCreate
from app.services import chat_service, memory_service

OLD = "2026-01-05T10:00:00"
QUESTION = "考试的事情得准备一下了"


class _CaptureProvider:
    available = True
    last_usage = None

    def __init__(self, text: str = "嗯。"):
        self.text = text
        self.seen: list[list[dict]] = []

    def chat(self, messages, temperature=None, json_mode=False):
        self.seen.append(messages)
        return self.text

    def chat_stream(self, messages, temperature=None, json_mode=False):
        self.seen.append(messages)
        yield self.text


def _observation(content: str, observed_at_local: str | None = None) -> dict:
    return memory_service.create_observation(
        ObservationCreate(
            content=content,
            source_text=f"我{content}",
            observed_at_local=observed_at_local,
        )
    )


def _plant_an_old_relevant_memory() -> None:
    """一条会被「最近 N 条」漏掉、但按相关度能找到的旧记忆。

    没有它，这个测试是空的 —— 两条路的记忆块都为空时，
    "逐字节相等"会**在 bug 存在的情况下也通过** ✗。
    """
    _observation("下个月要考试", OLD)
    for index in range(assembler.MEMORY_OBSERVATION_LIMIT + 2):
        _observation(f"无关的第 {index} 件事")


def test_the_streaming_path_uses_relevance_not_recency(client, monkeypatch):
    """★ 漏了 `query=` 时，这条会失败（检索退化成按时间取）。"""
    _plant_an_old_relevant_memory()
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", provider)

    list(chat_service.stream_chat(QUESTION))

    assert "下个月要考试" in provider.seen[-1][0]["content"]


def test_both_paths_build_the_same_system_prompt(client, monkeypatch):
    """两条路的 system prompt 必须**逐字节相等**。

    不是靠"两处都记得传参数"来保证，而是靠这条测试。
    """
    _plant_an_old_relevant_memory()
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", provider)

    list(chat_service.stream_chat(QUESTION))
    streaming_system = provider.seen[-1][0]["content"]

    # 独立地按同一条用户消息重建一次计划（不复用流式那条路的任何中间结果）
    user_message = next(
        message
        for message in reversed(chat_service.list_messages())
        if message["role"] == "user"
    )
    expected = assembler.plan_for_request(
        chat_service._raw_history_before(user_message["id"]),
        query=user_message["content"],
    ).system_prompt

    assert streaming_system == expected


def test_the_preview_matches_the_streaming_path(client, monkeypatch):
    """面板（预览）显示的，必须就是流式那条路会发出去的。

    面板用"最后一条用户消息"当检索词（预览时你正要发的那句还不存在），
    所以这里先发一次、再比。
    """
    _plant_an_old_relevant_memory()
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", provider)

    list(chat_service.stream_chat(QUESTION))
    streaming_system = provider.seen[-1][0]["content"]

    assert chat_service.build_context_plan().system_prompt == streaming_system


# ---------- 带图的消息不能把检索搞崩（用户实测 500） ----------

PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def test_the_preview_survives_an_image_message(client, monkeypatch):
    """★★ 真 bug：带图的消息在历史里 content 是**数组**，
    而 `build_context_plan()` 把最后一条用户消息直接当检索词 → `list.strip()` ✗。

    它逃过测试的原因是：图片和检索是**分两处**测的 ——
    没有任何一条测试把它们放在一起。所以这条测试存在的意义就是"把它们放一起"。
    """
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", provider)
    chat_service.send_chat("这是什么？", image=PNG)

    plan = chat_service.build_context_plan()  # 这里曾经 500

    assert plan.system_prompt


def test_the_query_comes_from_the_text_part_of_an_image_message(
    client, monkeypatch
):
    """带图消息当检索词时，取得是**文字那一段**，不是整个数组。"""
    _plant_an_old_relevant_memory()
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_service, "_provider", provider)
    chat_service.send_chat("考试的事情得准备一下了", image=PNG)

    # 直接走预览（它的检索词就是"最后一条用户消息"）
    plan = chat_service.build_context_plan()

    assert "下个月要考试" in plan.system_prompt


def test_a_non_string_query_never_crashes_the_search(client):
    """防御层：查不到就当空，**绝不 500** ——
    一个预览接口崩掉会把整页 UI 一起弄哑，而用户看不出原因 ✗。"""
    assert memory_service.search_memories(["not", "a", "string"]) == {
        "observations": [],
        "impressions": [],
    }
