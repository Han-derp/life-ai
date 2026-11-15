"""记忆导入（用户要求：把别处积累的事实一次性喂给她）。

## 核心设计

**不直接写事实表** ✗ —— 不变量 5 要求「每条事实都能追回一句话」✓，
所以导入 = **写一条用户消息** ✓ → 之后走**正常抽取流程** ✓
（出处 / 时间 / 标签全部自动对上 ✓）。

## 由此带来两条必须成立的排除

它可能几百行 ✓，**不是一次对话** ✓ → 不能进聊天页 ✗、也不能进每次请求的上下文 ✗
（预算只有 4000 token ✓）。
"""

import pytest

from app.core.exceptions import DomainValidationError
from app.services import chat_service


def test_importing_saves_one_message(client):
    result = chat_service.import_notes("下个月要考试\n周三 14:00 高数期中")

    assert result["imported_lines"] == 2
    assert chat_service.get_message(result["message_id"]) is not None


def test_the_imported_message_stays_out_of_the_chat_list(client):
    """★ 它是一批数据，**不是一次对话** ✓ —— 否则聊天页会出现几百行的气泡 ✗。"""
    message_id = chat_service.import_notes("下个月要考试")["message_id"]

    ids = {item["id"] for item in chat_service.list_messages()}

    assert message_id not in ids


def test_the_imported_message_stays_out_of_the_prompt_history(client):
    """★★ **这条最重要。**

    它可能几百行 ✓ —— 进了「最近聊天」就会把**每次请求的上下文撑爆** ✗
    （预算只有 4000 token ✓，那是钱 ✓）。
    """
    chat_service.import_notes("\n".join(f"第 {i} 条事实" for i in range(50)))

    contents = [item["content"] for item in chat_service.recent_history()]

    assert not any("第 3 条事实" in content for content in contents)


def test_blank_lines_and_comments_are_skipped(client):
    result = chat_service.import_notes("# 这是备注\n\n下个月要考试\n   \n")

    assert result["imported_lines"] == 1


def test_an_empty_import_is_rejected(client):
    with pytest.raises(DomainValidationError, match="没有可导入"):
        chat_service.import_notes("\n\n# 只有备注\n")


def test_too_many_lines_is_rejected(client):
    with pytest.raises(DomainValidationError, match="最多导入"):
        chat_service.import_notes("\n".join(f"第 {i} 条" for i in range(300)))


def test_the_import_endpoint_works(client):
    response = client.post("/api/v1/memory/import", json={"text": "下个月要考试"})

    assert response.status_code == 200
    assert response.json()["imported_lines"] == 1
