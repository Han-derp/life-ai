"""「你说过的话 —— 全部」✓（用户 2026-10-05 提的 ✓）。

## 用户的疑问

> 「我要看到的记忆是**所有他记下来到文件里的东西**，
>  难道只有被归纳出事实的原话才被写进文件吗？」

**他猜对了** ✗ —— `raw_entries`（L1）是**一行一条观察** ✓，
所以它只含"成了事实的那些原话" ✓，是个**子集** ✗。

**真正的全量在 `messages`** ✓ —— 这个文件守的就是这件事 ✓。
"""

from app.schemas.memory import ObservationCreate
from app.services import chat_service, memory_service


def _say(text: str) -> None:
    chat_service._save_message("user", text)


def test_it_lists_every_message_not_only_the_ones_that_became_facts(client):
    """★★ **这条就是用户要的东西** ✓。

    没被抽成事实的话**也必须在** ✓ —— 否则"他记下的一切"就是假的 ✓。
    """
    _say("今天天气不错")  # 这句不会变成任何事实 ✓
    _say("我下个月要考试")
    memory_service.create_observation(
        ObservationCreate(content="下个月要考试", source_text="我下个月要考试")
    )

    contents = [quote["content"] for quote in memory_service.list_quotes()]

    assert "今天天气不错" in contents, "没成事实的话被漏掉了 ✗"
    assert "我下个月要考试" in contents


def test_each_quote_knows_which_facts_came_out_of_it(client):
    """界面靠这个显示「→ N 条事实」✓（或"还没抽到" ✓）。

    ⚠️ **必须像抽取流程那样传 `message_id`** ✗✓ ——
    链接走的是 `raw_entries.message_id` ✓（migration 010 补的列 ✓），
    而它只有**抽取时**才会被填上 ✓（`create_observation(..., message_id=...)` ✓）。

    我第一版直接建了事实、没传 ✓ → 链接当然是空的 ✓
    （那**不是** bug ✓，是测试没照着真实流程走 ✓）。
    """
    message = chat_service._save_message("user", "我下个月要考试")
    _say("今天天气不错")
    observation = memory_service.create_observation(
        ObservationCreate(content="下个月要考试", source_text="我下个月要考试"),
        message_id=message["id"],
    )

    quotes = {quote["content"]: quote for quote in memory_service.list_quotes()}

    assert quotes["我下个月要考试"]["fact_ids"] == [observation["id"]]
    assert quotes["今天天气不错"]["fact_ids"] == []


def test_her_replies_are_not_in_it(client):
    """只取 `role='user'` ✓ —— 这是"**你**说过的话" ✓，不是聊天记录 ✓。"""
    _say("我说的话")
    chat_service._save_message("assistant", "她回的话")

    contents = [quote["content"] for quote in memory_service.list_quotes()]

    assert contents == ["我说的话"]


def test_an_imported_list_is_marked(client):
    """导入的清单也是一条消息 ✓ —— 界面要能标出来 ✓（它可能很长 ✓）。"""
    chat_service.import_notes("下个月要考试")

    quotes = memory_service.list_quotes()
    imported = [quote for quote in quotes if quote["imported"]]

    assert len(imported) == 1
    assert "下个月要考试" in imported[0]["content"]


def test_the_newest_quote_comes_first(client):
    _say("第一句")
    _say("第二句")

    contents = [quote["content"] for quote in memory_service.list_quotes()]

    assert contents == ["第二句", "第一句"]


def test_an_empty_database_returns_an_empty_list(client):
    assert memory_service.list_quotes() == []


def test_the_api_exposes_it(client):
    _say("我下个月要考试")

    response = client.get("/api/v1/memory/quotes")

    assert response.status_code == 200
    assert response.json()[0]["content"] == "我下个月要考试"
