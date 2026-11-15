from fastapi import APIRouter, Query

from app.schemas.commitment import (
    CommitmentCheckRequest,
    CommitmentCheckResponse,
    CommitmentCreate,
    CommitmentFromChatRequest,
    CommitmentFromChatResponse,
    CommitmentResponse,
    CommitmentReviseRequest,
)
from app.services import commitment_service

router = APIRouter(prefix="/commitments", tags=["commitments"])


@router.get("", response_model=list[CommitmentResponse])
def get_commitments(include_inactive: bool = Query(default=True)):
    """承诺列表。默认包含已改口 / 已撤销的——"她改过什么口"也是关系记忆。"""
    return commitment_service.list_commitments(include_inactive)


@router.post("", response_model=CommitmentResponse, status_code=201)
def post_commitment(data: CommitmentCreate):
    """自己记一条她答应过的事。

    `structured` 目前只支持 `{"quiet_hours": {"start": "23:00", "end": "08:00"}}`
    （`end` 传 null 表示当天 `start` 之后）。
    """
    return commitment_service.create_commitment(data)


@router.post("/from-chat", response_model=CommitmentFromChatResponse)
def post_commitment_from_chat(data: CommitmentFromChatRequest):
    """她从聊天里答应的一件事。没有你的原话会被拒（400）。"""
    return commitment_service.apply_chat_commitment(data)


@router.get("/{commitment_id}", response_model=CommitmentResponse)
def get_commitment(commitment_id: int):
    return commitment_service.get_commitment(commitment_id)


@router.post("/{commitment_id}/revise", response_model=CommitmentResponse)
def post_revise_commitment(commitment_id: int, data: CommitmentReviseRequest):
    """改一条承诺：**生成新版本，旧版本永久保留**（`superseded_by` 指过去）。"""
    return commitment_service.revise_commitment(commitment_id, data)


@router.post("/{commitment_id}/revoke", response_model=CommitmentResponse)
def post_revoke_commitment(commitment_id: int):
    return commitment_service.revoke_commitment(commitment_id)


@router.post("/{commitment_id}/check", response_model=CommitmentCheckResponse)
def post_check_commitment(commitment_id: int, data: CommitmentCheckRequest):
    """拿一批"她主动找你"的 UTC 时刻，核对这条承诺有没有被违反。

    ⚠️ 只该喂**主动联系**的时刻——**你自己来找她时她的回复不算违约**。
    S8 的主动联系接上之后，这里会由系统自动喂数据。
    """
    return commitment_service.violations_for(commitment_id, data.moments)


@router.get("/{commitment_id}/violations", response_model=CommitmentCheckResponse)
def get_commitment_violations(commitment_id: int):
    """拿她**真实的主动联系记录**核对这条承诺。

    和 `POST /{id}/check` 的区别：那个由你喂时刻（用于测试），
    这个直接读 S8 的 `proactive_contacts`——也就是她**真的**来找过你的时刻。
    """
    return commitment_service.violations_against_proactive(commitment_id)
