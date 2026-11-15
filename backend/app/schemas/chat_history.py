"""聊天记录编辑操作的 API contract。"""

from pydantic import BaseModel, Field


class EditMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageImpactResponse(BaseModel):
    """破坏性操作的**影响范围**。

    ``affected_observations`` 非空时，界面必须显式告诉用户
    "他还记着这件事" —— 删聊天记录 **不等于** 让他忘掉。
    """

    affected_observations: list[dict] = Field(default_factory=list)


class MessageOperationResponse(BaseModel):
    deleted_messages: int
    #: 显式选择"一起删掉"时才不为 0
    deleted_observations: int = 0
    #: 显式选择"连档案一起抹掉"、且确实抹掉的 L1 出处条数
    purged_raw_entries: int = 0
    #: 因为是某条判断的最后一条依据而被**拒绝**删除的（不变量 6）
    refused: list[dict] = Field(default_factory=list)


class EditMessageResponse(BaseModel):
    message: dict
    #: 必须把这句话显示出来，否则用户以为"改了就等于他没听过"
    note: str
    #: 只有 with_memory=true 时才可能非 0
    deleted_observations: int = 0
    #: 只有再带上 purge_archive=true 时才可能非 0
    purged_raw_entries: int = 0
    #: 因为是某条判断的最后一条依据而删不掉的（不变量 6）
    refused: list[dict] = Field(default_factory=list)
