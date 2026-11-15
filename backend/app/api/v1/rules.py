from fastapi import APIRouter, Query

from app.schemas.rule import (
    RuleCreate,
    RuleFromChatRequest,
    RuleFromChatResponse,
    RuleResponse,
    RuleUpdate,
)
from app.services import rule_service

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleResponse])
def get_rules(include_inactive: bool = Query(default=True)):
    """规则列表。默认**包含**已失效 / 已撤销的——"我关过什么"要可查。"""
    return rule_service.list_rules(include_inactive)


@router.post("", response_model=RuleResponse, status_code=201)
def post_rule(data: RuleCreate):
    """手动加一条规则（界面手改，优先级最高）。

    `expires_at_local` 传 `YYYY-MM-DD` 表示**当日有效**；不传表示永久。
    """
    return rule_service.create_rule(data)


@router.post("/from-chat", response_model=RuleFromChatResponse)
def post_rule_from_chat(data: RuleFromChatRequest):
    """她从聊天里理解出的一条规则。

    没有来源原话会被拒（400）；撞上人工规则时返回
    `overridden_by_manual=true` 并且**不覆盖**它。
    """
    return rule_service.apply_chat_rule(data)


@router.get("/{rule_id}", response_model=RuleResponse)
def get_rule(rule_id: int):
    return rule_service.get_rule(rule_id)


@router.patch("/{rule_id}", response_model=RuleResponse)
def patch_rule(rule_id: int, data: RuleUpdate):
    """界面手改。改过之后这条规则归你管，她从聊天再也覆盖不了。"""
    return rule_service.update_rule(rule_id, data)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int):
    rule_service.delete_rule(rule_id)
