from fastapi import APIRouter, Query

from app.schemas.impression_run import (
    ImpressionCandidateResponse,
    ImpressionGenerateRequest,
    ImpressionRunResponse,
)
from app.workflows import impression

router = APIRouter(prefix="/memory", tags=["memory"])

# ⚠️ 路径刻意用连字符而不是 `/impressions/candidates` ——
# 后者会被 `memory.router` 的 `GET /impressions/{impression_id}` 先匹配掉，
# 变成"impression_id='candidates'"的 422。
# （`/impression-lineages` 当初也是为同一个原因这么命名的。）


@router.get("/impression-candidates", response_model=list[ImpressionCandidateResponse])
def get_impression_candidates(min_count: int = Query(default=3, ge=2, le=20)):
    """哪些「同类」已经够格归纳了。

    「同类」＝**同标签**下、还没被当作依据用过的观察 ≥ `min_count` 条。
    返回空列表是正常的 —— 它意味着还没有任何标签攒够事件。
    """
    return impression.ripe_candidates(min_count)


@router.post("/impression-generate", response_model=ImpressionRunResponse)
def post_impression_generate(data: ImpressionGenerateRequest):
    """归纳成印象（L2 → L3）。

    - **指定 `observation_ids`** → 用这几条归纳（你自己挑的"同类"）。
      全新安装还没有标签时，走这条路。
    - **留空** → 自动：同标签下 ≥`min_count` 条还没用过的观察。

    判断**必须带依据**（不变量 5）：`create_impression()` 会拒绝空证据。
    """
    if data.observation_ids:
        created = impression.generate_from(data.observation_ids)
        return {
            "generated": 1 if created else 0,
            "candidates": [],
            "skipped_reason": (
                None if created else "模型认为这些事件之间没有共同点，所以没有形成判断。"
            ),
        }

    candidates = impression.ripe_candidates(data.min_count)
    if not candidates:
        return {
            "generated": 0,
            "candidates": [],
            "skipped_reason": (
                "没有够格的同类观察。归纳靠标签判断「同类」（同一标签下至少 "
                f"{data.min_count} 条），而现在还没有这样的分组。"
                "先给观察打标签，或者直接指定 observation_ids。"
            ),
        }

    counts = impression.generate_pending(data.min_count)
    return {
        "generated": counts["created"],
        "candidates": candidates,
        "skipped_reason": None,
        # 三条路都要能看见：新建判断 / 补同方向证据 / 记反例。
        # 尤其"补证据"—— 它**故意不新建判断**（R6），不说明会让人以为没生效。
        "detail": (
            f"新建判断 {counts['created']} 条 · "
            f"给已有判断补同方向证据 {counts['supported']} 次 · "
            f"记反例 {counts['countered']} 条 · "
            f"改写判断 {counts['revised']} 条"
        ),
    }
