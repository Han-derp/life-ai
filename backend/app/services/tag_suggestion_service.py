"""标签建议（设计裁决 R7 / R8）。

她**可以建议**新标签，但**不能自己创建**（MEMORY_DESIGN §6.4）。
抽取时匹不上任何已有标签 —— 那就提建议，**绝不硬塞**：

> 硬塞会污染标签体系，而标签是印象系统的检索基础。
> 宁可不挂，也不要挂错。

三条硬约束（裁决 R8）：

1. 同一 ``(name, dimension)`` **只累加 count，不新增行**
   —— 否则你看到 20 条"想睡觉"，无从下手
2. **``rejected`` 的不许再建议** —— 否则她反复提你拒绝过的东西，
   那是关系里最烦人的行为之一
3. 批准后写入 ``tags`` 并**清理该建议** —— 避免"已批准但还挂在待办里"
"""

from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.memory import TagCreate
from app.services import memory_service

# 和 `tags.dimension` 的 CHECK 保持一致（migration 003）
DIMENSIONS = ("state", "domain", "object", "pattern", "time")


def _now_iso() -> str:
    return to_utc_iso(utc_now())


def suggest(
    name: str,
    dimension: str,
    reason: str | None = None,
    source_observation_id: int | None = None,
) -> dict | None:
    """记一条建议。

    返回建议行；**名字非法 / 已被拒绝 / 标签已存在**时返回 ``None``
    （三种情况都不是错误，只是"这条不用记"）。
    """
    name = (name or "").strip()
    if not name or dimension not in DIMENSIONS:
        return None

    with get_connection() as conn:
        # 约束 2：拒绝过的不再建议（**在累加之前**拦住，count 也不许涨）
        rejected = conn.execute(
            """
            SELECT 1 FROM tag_suggestions
            WHERE name = ? AND dimension = ? AND status = 'rejected'
            """,
            (name, dimension),
        ).fetchone()
        if rejected:
            return None

        # 已经有这个标签了，就没必要建议（tags.name 是全局唯一的）
        if conn.execute("SELECT 1 FROM tags WHERE name = ?", (name,)).fetchone():
            return None

        now = _now_iso()
        # 约束 1：同一 (name, dimension) 只累加 count
        conn.execute(
            """
            INSERT INTO tag_suggestions (
                name, dimension, reason, source_observation_id,
                count, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, 'pending', ?, ?)
            ON CONFLICT(name, dimension) DO UPDATE SET
                count = count + 1,
                reason = COALESCE(excluded.reason, reason),
                source_observation_id = COALESCE(
                    excluded.source_observation_id, source_observation_id
                ),
                updated_at = excluded.updated_at
            """,
            (name, dimension, reason, source_observation_id, now, now),
        )
        row = conn.execute(
            """
            SELECT * FROM tag_suggestions
            WHERE name = ? AND dimension = ?
            """,
            (name, dimension),
        ).fetchone()
        return dict(row)


def list_pending(order_by: str = "count") -> list[dict]:
    """待批准的建议。

    默认**按被建议的次数降序** —— 她反复注意到的那件事排最前。
    这正是 ``count`` 字段存在的理由：它本身就是一种洞察。
    """
    column = "count" if order_by == "count" else "updated_at"
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM tag_suggestions
            WHERE status = 'pending'
            ORDER BY {column} DESC, id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def approve(suggestion_id: int) -> dict:
    """约束 3：批准 → 写入 ``tags`` → 清理这条建议。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tag_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("这条标签建议不存在。")

    tag = None
    try:
        tag = memory_service.create_tag(
            TagCreate(name=row["name"], dimension=row["dimension"])
        )
    except DomainValidationError:
        # 标签已经存在（名字全局唯一）—— 那也算"批过了"，照样清理建议
        tag = None

    with get_connection() as conn:
        conn.execute("DELETE FROM tag_suggestions WHERE id = ?", (suggestion_id,))

    return {"name": row["name"], "tag": tag}


def reject(suggestion_id: int) -> dict:
    """约束 2：拒绝之后，这个名字**永远不会再被建议**。"""
    now = _now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tag_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("这条标签建议不存在。")

        conn.execute(
            """
            UPDATE tag_suggestions
            SET status = 'rejected', updated_at = ?
            WHERE id = ?
            """,
            (now, suggestion_id),
        )

    return {"name": row["name"], "status": "rejected"}
