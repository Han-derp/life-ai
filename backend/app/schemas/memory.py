"""记忆库的 API contract（DEVELOPMENT_PLAN v2.0 · S4）。

L1 原始资料 / L2 事件观察 / 标签。
L3 印象与修订在下一个子步（那时才会用到 confidence 的迭代规则）。
"""

from typing import Literal

from pydantic import BaseModel, Field

TagDimension = Literal["state", "domain", "object", "pattern", "time"]


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    dimension: TagDimension


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    dimension: TagDimension | None = None


class TagResponse(BaseModel):
    id: int
    name: str
    dimension: str
    observation_count: int = 0


class RawEntryResponse(BaseModel):
    """L1 原话 —— 事实的**出处** ✓。

    ⚠️ **只读** ✓ —— L1 只追加、永不改写 ✓（它是事实源 ✓）。
    "改记忆"改的是 L2/L3 ✓，不是这一层 ✓。

    ⚠️ **它是子集** ✗ —— 只有"成了事实的那些原话"才在 L1 ✓。
    "你说过的所有话"看 `QuoteResponse` ✓。
    """

    id: int
    content: str
    source_type: str
    occurred_at: str | None = None
    created_at: str
    #: 能在聊天里定位到那句话（migration 010 补的列 ✓）
    message_id: int | None = None
    #: **依赖它的那些事实** ✓ —— 删这条会影响谁
    observation_ids: list[int] = Field(default_factory=list)
    #: 那些事实又被哪些判断当依据用着 ✓
    impression_ids: list[int] = Field(default_factory=list)


class QuoteResponse(BaseModel):
    """**你说过的一句话 —— 全量** ✓（`messages` 里的每一句 `role='user'`）。

    ⚠️ 和 `RawEntryResponse` **不是一回事** ✗：

    - `RawEntryResponse` = **L1 出处** ✓，只含"成了事实的"原话 ✓（**子集**）
    - `QuoteResponse` = **你说过的所有话** ✓（这才是"他记下的一切" ✓）
    """

    id: int
    content: str
    created_at: str
    #: 从这句话里抽出来的事实（**可能为空** ✓ —— 抽不到很正常 ✓）
    fact_ids: list[int] = Field(default_factory=list)
    #: 是不是"导入的清单"（那种会一次带很多条 ✓）
    imported: bool = False


class ObservationCreate(BaseModel):
    """一条 L2 观察。

    ``source_text`` 是**必填**的：它对应 L1 原始资料。
    不变量 5 —— 没有出处，她不许说"我记得"；所以观察不允许无源存在。
    """

    content: str = Field(min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=4000)
    observed_at_local: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    tag_ids: list[int] = Field(default_factory=list, max_length=10)


class ObservationTagsUpdate(BaseModel):
    tag_ids: list[int] = Field(default_factory=list, max_length=10)


class ObservationResponse(BaseModel):
    id: int
    content: str
    observed_at: str
    observed_at_local: str
    local_date: str
    confidence: float
    raw_entry_id: int | None
    source_text: str | None  # 出处原文；面板要能"点开看原话"
    tags: list[TagResponse] = Field(default_factory=list)
    excluded: bool = False


# ---------- L3 印象 ----------

ImpressionAttitude = Literal["none", "agreed", "disputed"]


class ImpressionCreate(BaseModel):
    """一条 L3 印象。

    ``evidence_ids`` 是**必填**的：不变量 5 —— 答不出依据的判断不许说出口。
    LLM 归纳（S4 的下一个子步）会走同一个入口。
    """

    content: str = Field(min_length=1, max_length=600)
    evidence_ids: list[int] = Field(min_length=1, max_length=50)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ImpressionAttitudeUpdate(BaseModel):
    attitude: ImpressionAttitude


class ImpressionCounterexampleRequest(BaseModel):
    observation_id: int | None = None


class ImpressionReviseRequest(BaseModel):
    content: str = Field(min_length=1, max_length=600)
    evidence_ids: list[int] | None = Field(default=None, max_length=50)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ImpressionResponse(BaseModel):
    id: int
    content: str
    generation: int
    confidence: float
    status: str
    attitude: str
    counterexamples: int
    superseded_by: int | None
    needs_revision: bool  # 反例攒够 3 次（计算属性，不是存字段）
    evidence: list[ObservationResponse] = Field(default_factory=list)
    excluded: bool = False


class SupportingObservationsResponse(BaseModel):
    """「同类事件累积 N 次」的素材够不够。"""

    tag_id: int
    count: int
    min_count: int
    ready: bool
    observations: list[ObservationResponse] = Field(default_factory=list)


# ---------- 演化史（S5「她眼中的我」） ----------


class ImpressionHistoryEntry(ImpressionResponse):
    """演化史里的一代。最后一条是"现在的看法"。"""

    is_current: bool = False


class ImpressionLineageResponse(BaseModel):
    """一条判断的完整演化——「她眼中的我」的展示单位。

    界面用 ``history`` 组织成叙事（"我两个月前觉得…后来…现在我觉得…"），
    而不是把代际列成版本号。
    """

    current_id: int
    generation: int
    current: ImpressionResponse
    history: list[ImpressionHistoryEntry] = Field(default_factory=list)
