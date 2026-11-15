import json

from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.action_plan import ActionPlanCreate
from app.services import action_step_service


def _row(row) -> dict:
    """行 → dict ✓。

    ⚠️ `steps_json` 的解析**兜底** ✗ —— 坏一行坏数据不该让整个列表 500 ✓。
    ⚠️ `blocking_type` 可能是 `NULL` ✓（手工建的计划 / 模型没判断出来 ✓）——
    用 `keys()` 先看一眼 ✓（万一列的迁移没跑到 ✓）。
    """
    try:
        steps = json.loads(row["steps_json"])
    except (TypeError, ValueError):
        steps = []
    if not isinstance(steps, list):
        steps = []

    def _json_list(key: str) -> list:
        if key not in row.keys():
            return []
        try:
            value = json.loads(row[key])
            return value if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []

    return {
        "id": row["id"],
        "title": row["title"],
        "reason": row["reason"],
        "minimum_plan": row["minimum_plan"],
        "fallback_plan": row["fallback_plan"],
        "steps": steps,
        "status": row["status"],
        "blocking_type": row["blocking_type"] if "blocking_type" in row.keys() else None,
        "assumptions": _json_list("assumptions_json"),
        "needs_confirmation": _json_list("needs_confirmation_json"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _with_steps(plan: dict) -> dict:
    """给计划补上**可执行步骤** ✓（`action_plan_steps` 表 ✓）。

    两个字段不一样 ✓，别混 ✗：

    - `plan["steps"]` ✓ = **原始草案**（AI 返回的 JSON ✓，只读 ✓）
    - `plan["executable_steps"]` ✓ = **可执行步骤**（有状态 / 耗时 / 落地链接 ✓）
    """
    if plan is None:
        return plan
    plan["executable_steps"] = action_step_service.list_steps(plan["id"])
    return plan


def create_plan(data: ActionPlanCreate) -> dict:
    """存一份计划 ✓ —— 状态从 `draft` 开始 ✓，**同时物化出可执行步骤** ✓。

    ⚠️ **这里不确认** ✗ —— 确认是用户的动作 ✓（`confirm_plan()` ✓）。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO action_plans (
                title, reason, minimum_plan, fallback_plan,
                steps_json, status, blocking_type, assumptions_json, needs_confirmation_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (
                data.title.strip(),
                data.reason,
                data.minimum_plan,
                data.fallback_plan,
                json.dumps(
                    [step.model_dump() for step in data.steps], ensure_ascii=False
                ),
                data.blocking_type,
                json.dumps(data.assumptions, ensure_ascii=False),
                json.dumps(data.needs_confirmation, ensure_ascii=False),
                now,
                now,
            ),
        )
        plan_id = cur.lastrowid
        action_step_service.create_steps_for_plan(conn, plan_id, data.steps, now)

    # ⚠️ **出了 `with` 再读** ✗ —— `list_steps()` 会**开另一个连接** ✓，
    # 在事务里读**看不到还没提交的写入** ✗
    # （2026-10-05：20 条测试全红就是这么来的 ✓）。
    with get_connection() as conn:
        created = conn.execute(
            "SELECT * FROM action_plans WHERE id = ?", (plan_id,)
        ).fetchone()
    return _with_steps(_row(created))


def get_plan(plan_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM action_plans WHERE id = ?", (plan_id,)
        ).fetchone()
    if row is None:
        raise NotFoundError("行动计划不存在。")
    return _with_steps(_row(row))


def list_plans(status: str | None = None) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM action_plans WHERE (? IS NULL OR status = ?) ORDER BY id DESC",
            (status, status),
        ).fetchall()
    return [_with_steps(_row(row)) for row in rows]


def confirm_plan(plan_id: int) -> dict:
    """把一份计划**确认**下来 ✓。

    ⚠️ 只有 `draft` / `adjusted` 能确认 ✗ —— 已确认的不能重复确认 ✓，
    别的状态（`active` / `completed` / `cancelled`）也不行 ✓。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM action_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("行动计划不存在。")
        if row["status"] not in {"draft", "adjusted"}:
            raise DomainValidationError("当前计划不能确认。")
        conn.execute("UPDATE action_plan_steps SET confirmed_minutes=COALESCE(confirmed_minutes, estimated_minutes, minutes) WHERE plan_id=?", (plan_id,))
        conn.execute(
            "UPDATE action_plans SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (now, plan_id),
        )
        return _with_steps(
            _row(
                conn.execute(
                    "SELECT * FROM action_plans WHERE id = ?", (plan_id,)
                ).fetchone()
            )
        )


def refresh_plan_status(plan_id: int) -> dict:
    """根据可执行步骤的真实状态更新计划状态。

    这是唯一的计划状态推进入口，避免接口各自修改状态造成分叉。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        plan = conn.execute("SELECT * FROM action_plans WHERE id = ?", (plan_id,)).fetchone()
        if plan is None:
            raise NotFoundError("行动计划不存在。")
        if plan["status"] in {"draft", "cancelled"}:
            return _with_steps(_row(plan))
        rows = conn.execute("SELECT status FROM action_plan_steps WHERE plan_id = ?", (plan_id,)).fetchall()
        statuses = [r["status"] for r in rows]
        if statuses and all(x in {"completed", "skipped"} for x in statuses):
            new_status = "completed" if all(x == "completed" for x in statuses) else "adjusted"
        elif any(x == "active" for x in statuses):
            new_status = "active"
        else:
            new_status = "confirmed"
        if new_status != plan["status"]:
            conn.execute("UPDATE action_plans SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, plan_id))
            plan = conn.execute("SELECT * FROM action_plans WHERE id = ?", (plan_id,)).fetchone()
    return _with_steps(_row(plan))
