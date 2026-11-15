from fastapi import APIRouter, Query

from app.schemas.memory import (
    ImpressionAttitudeUpdate,
    ImpressionCounterexampleRequest,
    ImpressionCreate,
    ImpressionLineageResponse,
    ImpressionResponse,
    ImpressionReviseRequest,
    ObservationCreate,
    ObservationResponse,
    ObservationTagsUpdate,
    QuoteResponse,
    RawEntryResponse,
    SupportingObservationsResponse,
    TagCreate,
    TagResponse,
    TagUpdate,
)
from app.services import memory_service

router = APIRouter(prefix="/memory", tags=["memory"])


# ---------- 你说过的话（全量） ----------


@router.get("/quotes", response_model=list[QuoteResponse])
def get_quotes(limit: int = Query(default=200, ge=1, le=500)):
    """**你说过的话 —— 全部** ✓（不只是成了事实的那些）。

    ⚠️ 和 `/raw-entries` **不是一回事** ✗：

    - `/raw-entries` = **L1 出处** ✓（只含"成了事实的"原话 ✓，**子集** ✗）
    - `/quotes` = **你说过的每一句** ✓（`messages` 里的全量 ✓✓）

    每句带 `fact_ids` ✓ —— 界面据此显示「→ N 条事实」或「还没抽到」✓。

    用户 2026-10-05 明确要求：
    「**我要看到的记忆是所有他记下来到文件里的东西**」✓
    """
    return memory_service.list_quotes(limit=limit)


# ---------- L1 原话（只读） ----------


@router.get("/raw-entries", response_model=list[RawEntryResponse])
def get_raw_entries(limit: int = Query(default=200, ge=1, le=500)):
    """列出 L1 原话 —— 事实的**出处** ✓。

    ⚠️ **只读** ✓：L1 只追加、永不改写 ✓（它是事实源 ✓）。
    "改记忆"改的是 L2 / L3 ✓，不是这里 ✓。

    每条带 `observation_ids` / `impression_ids` ✓ ——
    界面据此告诉你「删这条会动到哪些事实和判断」✓。
    """
    return memory_service.list_raw_entries(limit=limit)


# ---------- L2 观察 ----------


@router.get("/observations", response_model=list[ObservationResponse])
def get_observations(
    local_date: str | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    return memory_service.list_observations(local_date, tag_id, limit)


@router.post("/observations", response_model=ObservationResponse, status_code=201)
def post_observation(data: ObservationCreate):
    """记一条 L2 观察。

    `source_text`（出处原文）是必填的——没有出处就不许存在（不变量 5）。
    """
    return memory_service.create_observation(data)


@router.get("/observations/{observation_id}", response_model=ObservationResponse)
def get_observation(observation_id: int):
    return memory_service.get_observation(observation_id)


@router.put("/observations/{observation_id}/tags", response_model=ObservationResponse)
def put_observation_tags(observation_id: int, data: ObservationTagsUpdate):
    """整体替换一条观察的标签集合。传空数组即清空分类（观察本身不动）。"""
    return memory_service.set_observation_tags(observation_id, data)


@router.delete("/observations/{observation_id}", status_code=204)
def delete_observation(observation_id: int):
    """删除一条观察。L1 原始资料保留（只追加、永不改写）。"""
    memory_service.delete_observation(observation_id)


# ---------- 标签 ----------


@router.get("/tags", response_model=list[TagResponse])
def get_tags():
    return memory_service.list_tags()


@router.post("/tags", response_model=TagResponse, status_code=201)
def post_tag(data: TagCreate):
    return memory_service.create_tag(data)


@router.patch("/tags/{tag_id}", response_model=TagResponse)
def patch_tag(tag_id: int, data: TagUpdate):
    return memory_service.update_tag(tag_id, data)


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int):
    """删除标签。**只取消分类，不删记忆**——观察会变成未分类，仍然存在。"""
    memory_service.delete_tag(tag_id)


# ---------- L3 印象 ----------


@router.get("/impressions", response_model=list[ImpressionResponse])
def get_impressions(include_superseded: bool = Query(default=True)):
    """印象列表。默认带历史代际——"旧版本永久保留"是设计的一部分。"""
    return memory_service.list_impressions(include_superseded)


@router.post("/impressions", response_model=ImpressionResponse, status_code=201)
def post_impression(data: ImpressionCreate):
    """建一条印象。

    `evidence_ids`（依据事件）必填——答不出依据的判断不许说出口（不变量 5）。
    """
    return memory_service.create_impression(
        data.content,
        data.evidence_ids,
        data.confidence,
    )


@router.get("/impressions/{impression_id}", response_model=ImpressionResponse)
def get_impression(impression_id: int):
    return memory_service.get_impression(impression_id)


@router.post("/impressions/{impression_id}/attitude", response_model=ImpressionResponse)
def post_impression_attitude(impression_id: int, data: ImpressionAttitudeUpdate):
    """认同 / 异议 / 撤回表态。

    **没有"删除印象"这个动作**：事实可以删，判断只能标异议。
    """
    return memory_service.set_impression_attitude(impression_id, data.attitude)


@router.post(
    "/impressions/{impression_id}/counterexamples",
    response_model=ImpressionResponse,
)
def post_counterexample(impression_id: int, data: ImpressionCounterexampleRequest):
    """记一条同类反例：1 次标记 → 2 次降置信度 → 3 次该修订了。"""
    return memory_service.record_counterexample(impression_id, data.observation_id)


@router.post("/impressions/{impression_id}/revise", response_model=ImpressionResponse)
def post_revise_impression(impression_id: int, data: ImpressionReviseRequest):
    """生成新一代印象。旧版本永久保留（`superseded_by` 指过去）。"""
    return memory_service.revise_impression(
        impression_id,
        data.content,
        data.evidence_ids,
        data.confidence,
    )


@router.get(
    "/tags/{tag_id}/supporting",
    response_model=SupportingObservationsResponse,
)
def get_supporting_observations(
    tag_id: int,
    min_count: int = Query(default=3, ge=1, le=100),
):
    """挂了这个标签的事件够不够归纳成一条印象（默认够 3 条才算够）。"""
    return memory_service.observations_supporting(tag_id, min_count)


# ---------- 演化史（S5「她眼中的我」） ----------


@router.get(
    "/impression-lineages",
    response_model=list[ImpressionLineageResponse],
)
def get_impression_lineages():
    """按代际链聚合的印象——「她眼中的我」的展示单位。

    路径刻意避开 `/impressions/{impression_id}`，
    免得 "lineages" 被当成 id 去解析（那是 V1 里 `local_date` 那类坑的近亲）。
    """
    return memory_service.list_impression_lineages()
