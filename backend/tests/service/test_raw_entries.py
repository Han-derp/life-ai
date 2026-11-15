"""L1 原话的列表接口 —— 「完整记忆」视图的地基 ✓。

⚠️ **只读** ✓ —— L1 只追加、永不改写 ✓
（`memory_service.delete_observation()` 的 docstring 里写着原因：**它是事实源** ✓）。

界面靠这里返回的 `observation_ids` / `impression_ids` 告诉你
「**删这条会动到哪些事实和判断**」✓ —— 用户拍板的设计是：
判断**永不删除** ✓、事实**不给文字编辑** ✓（只能删掉重说 ✓）。
"""

from app.schemas.memory import ObservationCreate
from app.services import memory_service


def _observe(content: str, source: str) -> dict:
    return memory_service.create_observation(
        ObservationCreate(content=content, source_text=source)
    )


def test_it_lists_the_quote_each_fact_came_from(client):
    _observe("下个月要考试", "我下个月要考试")

    entries = memory_service.list_raw_entries()

    assert [entry["content"] for entry in entries] == ["我下个月要考试"]


def test_each_quote_knows_which_facts_depend_on_it(client):
    """★ 界面靠这个说「删这条会动到哪些事实」✓。"""
    _observe("下个月要考试", "我下个月要考试")
    observation = memory_service.list_observations()[0]

    entry = memory_service.list_raw_entries()[0]

    assert entry["observation_ids"] == [observation["id"]]


def test_the_newest_quote_comes_first(client):
    """记忆视图里「最新说的」排最上面 ✓。"""
    _observe("A", "第一句")
    _observe("B", "第二句")

    contents = [entry["content"] for entry in memory_service.list_raw_entries()]

    assert contents == ["第二句", "第一句"]


def test_an_empty_database_returns_an_empty_list(client):
    assert memory_service.list_raw_entries() == []


def test_the_api_exposes_it_read_only(client):
    _observe("下个月要考试", "我下个月要考试")

    response = client.get("/api/v1/memory/raw-entries")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["content"] == "我下个月要考试"
    assert body[0]["observation_ids"]
