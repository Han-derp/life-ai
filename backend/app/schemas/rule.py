"""她规则的 API contract（DEVELOPMENT_PLAN v2.0 · S7）。"""

from typing import Literal

from pydantic import BaseModel, Field

RuleSource = Literal["manual", "chat"]
RuleStatus = Literal["active", "paused", "revoked"]


class RuleCreate(BaseModel):
    content: str = Field(min_length=1, max_length=300)
    source_text: str | None = Field(default=None, max_length=1000)
    effective_from_local: str | None = None
    # 传日期（YYYY-MM-DD）= **当日有效**；传具体时刻 = 到那一刻为止；
    # 不传 = 永久。见 rule_service._normalize_expiry。
    expires_at_local: str | None = None
    note: str | None = Field(default=None, max_length=500)


class RuleUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=300)
    source_text: str | None = Field(default=None, max_length=1000)
    expires_at_local: str | None = None
    status: RuleStatus | None = None
    note: str | None = Field(default=None, max_length=500)


class RuleFromChatRequest(BaseModel):
    """她从聊天里理解出的一条规则。

    ``source_text`` 是**必填**的：她不许凭空新增规则，只能从你的原话提取
    （MEMORY_DESIGN §5.4 硬规则 3）。
    """

    content: str = Field(min_length=1, max_length=300)
    source_text: str = Field(min_length=1, max_length=1000)
    expires_at_local: str | None = None


class RuleResponse(BaseModel):
    id: int
    content: str
    source_text: str | None
    source: str
    effective_from: str
    effective_from_local: str
    expires_at: str | None
    expires_at_local: str | None
    status: str
    note: str | None
    is_permanent: bool  # 没有期限 = 永久
    in_effect: bool  # 实时算，不落库


class RuleFromChatResponse(BaseModel):
    rule: RuleResponse
    created: bool
    overridden_by_manual: bool
