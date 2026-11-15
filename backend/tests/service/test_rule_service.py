"""S7 她的规则。

覆盖 DEVELOPMENT_PLAN v2.0 · S7 完成判据里**不需要模型**的部分：

- 界面能手动增删改，且**优先级高于聊天理解**（硬规则 4）
- 期限："今天" ≠ "永久"（硬规则 2）
- AI **不能凭空新增规则**：没有来源原话就不许建（硬规则 3）
"""

import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.rule import RuleCreate, RuleFromChatRequest, RuleUpdate
from app.services import rule_service


def _rule(content: str = "23:00 之后不主动联系", **kwargs) -> dict:
    return rule_service.create_rule(RuleCreate(content=content, **kwargs))


# ---------- 增删改查 ----------


def test_create_and_list_rules():
    rule = _rule(source_text="以后十一点以后别找我")

    assert rule["source"] == "manual"
    assert rule["source_text"] == "以后十一点以后别找我"
    assert rule["is_permanent"] is True
    assert rule["in_effect"] is True
    assert rule["status"] == "active"

    assert [item["id"] for item in rule_service.list_rules()] == [rule["id"]]


def test_update_changes_content_and_takes_ownership():
    chat_rule = rule_service.apply_chat_rule(
        RuleFromChatRequest(content="十点后别找我", source_text="以后十点以后别找我")
    )["rule"]
    assert chat_rule["source"] == "chat"

    updated = rule_service.update_rule(
        chat_rule["id"],
        RuleUpdate(content="十一点后别找我"),
    )

    assert updated["content"] == "十一点后别找我"
    assert updated["source"] == "manual"  # 手改之后这条归你管


def test_delete_rule():
    rule = _rule()
    rule_service.delete_rule(rule["id"])

    assert rule_service.list_rules() == []
    with pytest.raises(NotFoundError):
        rule_service.get_rule(rule["id"])


def test_unknown_rule_raises():
    with pytest.raises(NotFoundError):
        rule_service.update_rule(999, RuleUpdate(content="x"))


# ---------- 期限（硬规则 2） ----------


def test_date_only_expiry_means_the_rest_of_that_day():
    """完成判据："我今天不想被打扰" 生成的是**当日**规则，不是永久规则。"""
    rule = _rule("今天别打扰我", expires_at_local="2026-06-01")

    assert rule["is_permanent"] is False
    # 到当地时间次日 00:00 为止 → UTC 是当天 16:00
    assert rule["expires_at"] == "2026-06-01T16:00:00Z"


def test_explicit_expiry_is_kept():
    rule = _rule("安静到今晚", expires_at_local="2026-06-01T23:00")

    assert rule["expires_at"] == "2026-06-01T15:00:00Z"
    assert rule["is_permanent"] is False


def test_expired_rule_is_not_in_effect_but_still_visible():
    rule = _rule("过期了", expires_at_local="2000-01-01")

    assert rule["in_effect"] is False
    # 默认列表里还看得到（"我关过什么"要可查）
    assert rule["id"] in [item["id"] for item in rule_service.list_rules()]
    assert rule["id"] not in [
        item["id"] for item in rule_service.list_rules(include_inactive=False)
    ]


def test_paused_rule_is_not_in_effect():
    rule = _rule("先暂停")

    paused = rule_service.update_rule(rule["id"], RuleUpdate(status="paused"))

    assert paused["status"] == "paused"
    assert paused["in_effect"] is False


def test_invalid_expiry_is_rejected():
    with pytest.raises(DomainValidationError):
        _rule("坏期限", expires_at_local="not-a-date")


# ---------- 只能是你的原话（硬规则 3） ----------


def test_chat_rule_requires_source_text():
    """她不许凭空新增规则——没有原话就不许建。"""
    with pytest.raises(DomainValidationError):
        rule_service.apply_chat_rule(
            RuleFromChatRequest(content="别在晚上找我", source_text="   ")
        )


def test_chat_rule_is_created_with_its_source():
    result = rule_service.apply_chat_rule(
        RuleFromChatRequest(content="十一点后别找我", source_text="以后十一点以后别找我")
    )

    assert result["created"] is True
    assert result["overridden_by_manual"] is False
    assert result["rule"]["source"] == "chat"
    assert result["rule"]["source_text"] == "以后十一点以后别找我"


# ---------- 界面手改优先（硬规则 4） ----------


def test_manual_rule_wins_over_the_same_chat_rule():
    """完成判据：界面手改的优先级高于聊天理解。"""
    manual = _rule("十一点后别找我", source_text="我说的")

    result = rule_service.apply_chat_rule(
        RuleFromChatRequest(content="十一点后别找我", source_text="以后十一点以后别找我")
    )

    assert result["created"] is False
    assert result["overridden_by_manual"] is True
    assert result["rule"]["id"] == manual["id"]
    # 人工规则没被改写
    assert result["rule"]["source"] == "manual"
    assert result["rule"]["source_text"] == "我说的"
    # 也没有偷偷多出一条
    assert len(rule_service.list_rules()) == 1


def test_chat_rule_is_not_blocked_by_an_unrelated_manual_rule():
    _rule("十一点后别找我")

    result = rule_service.apply_chat_rule(
        RuleFromChatRequest(content="别在开会时找我", source_text="我开会的时候别打扰我")
    )

    assert result["created"] is True
    assert result["overridden_by_manual"] is False


def test_editing_a_chat_rule_protects_it_from_later_chat_updates():
    chat_rule = rule_service.apply_chat_rule(
        RuleFromChatRequest(content="十点后别找我", source_text="以后十点以后别找我")
    )["rule"]

    rule_service.update_rule(chat_rule["id"], RuleUpdate(content="十一点后别找我"))

    later = rule_service.apply_chat_rule(
        RuleFromChatRequest(content="十一点后别找我", source_text="以后十一点以后别找我")
    )

    assert later["overridden_by_manual"] is True
    assert later["rule"]["source"] == "manual"
