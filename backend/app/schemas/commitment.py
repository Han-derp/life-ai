"""承诺的 API contract（DEVELOPMENT_PLAN v2.0 · S7）。

承诺 = **她答应过你什么**（关系记忆 LR 层优先做的那一项）。
它必须是**结构化**的，否则没法验证她守没守约（MEMORY_DESIGN §4.5-A）。
"""

from typing import Literal

from pydantic import BaseModel, Field


class CommitmentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=600)  # 她的回应原话
    source_text: str | None = Field(default=None, max_length=1000)  # 你的原话
    # 目前只支持 {"quiet_hours": {"start": "23:00", "end": "08:00" or null}}
    structured: dict | None = None
    effective_from_local: str | None = None
    expires_at_local: str | None = None


class CommitmentFromChatRequest(BaseModel):
    """她从聊天里答应你的一件事。

    ``source_text``（你的原话）是**必填**的：她要答应什么，得先有你那句话。
    """

    content: str = Field(min_length=1, max_length=600)
    source_text: str = Field(min_length=1, max_length=1000)
    structured: dict | None = None
    expires_at_local: str | None = None


class CommitmentReviseRequest(BaseModel):
    """改一条承诺。

    **旧版本永久保留**（`superseded_by` 指过去）——"她改过口"本身是关系记忆的一部分。
    """

    content: str | None = Field(default=None, min_length=1, max_length=600)
    source_text: str | None = Field(default=None, max_length=1000)
    structured: dict | None = None
    expires_at_local: str | None = None


class CommitmentCheckRequest(BaseModel):
    """拿一批"她主动找你"的 UTC 时刻来核对这条承诺。"""

    moments: list[str] = Field(default_factory=list, max_length=500)


class CommitmentResponse(BaseModel):
    id: int
    content: str
    source_text: str | None
    structured: dict | None
    window_text: str | None  # 给人看的："23:00 之后不主动找你"
    effective_from: str
    effective_from_local: str
    expires_at: str | None
    expires_at_local: str | None
    status: str
    created_via: str
    superseded_by: int | None
    is_permanent: bool
    in_effect: bool


class CommitmentFromChatResponse(BaseModel):
    commitment: CommitmentResponse
    created: bool
    overridden_by_manual: bool


class CommitmentViolation(BaseModel):
    moment: str
    moment_local: str


class CommitmentCheckResponse(BaseModel):
    commitment_id: int
    checkable: bool  # 没有可执行结构就无从检查
    window: dict | None = None
    checked: int = 0
    violations: list[CommitmentViolation] = Field(default_factory=list)


CommitmentStatus = Literal["active", "changed", "revoked"]
