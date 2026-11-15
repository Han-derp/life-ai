"""「行动步骤」—— 从**按一下就完事**变成**真的在跑** ✓（③ ⑤）。

## 这一层管什么

- **状态机** ✓：`pending → active → completed / skipped` ✓
- **实际耗时** ✓（番茄钟结束时写回来 ✓ —— `pomodoro_sessions.action_step_id` ✓）
- **落地** ✓：把一步变成 DDL / 日程（③ ✓），或者**什么都不做** ✓
- **调整** ✓：标题 / 时长 / 优先级（"确认前可以改" ✓）

## 三条规矩（用户 2026-10-05 明确要求 ✓）

1. **不默认全塞进日历** ✗ —— `materialize_step()` 默认 `kind="none"` ✓，
   要 DDL / 日程必须**明确指定** ✓（"否则容易制造大量无效安排" ✓）
2. **番茄钟必须绑到具体步骤** ✓ —— 不绑就没法做计划偏差分析 ✗
3. **落地之后同步状态** ✓ —— 步骤完成 → 对应的 DDL / 日程也标记完成 ✓
"""

import logging

from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.schemas.deadline import DeadlineCreate
from app.schemas.schedule import ScheduleCreate
from app.services import deadline_service, schedule_service

logger = logging.getLogger(__name__)


def _row(row) -> dict:
    return dict(row)


def _step(conn, plan_id: int, step_id: int):
    """取一步 ✓（顺带校验"它属于这个计划" ✓ —— 不能跨计划点 ✓）。"""
    row = conn.execute(
        "SELECT * FROM action_plan_steps WHERE id = ? AND plan_id = ?",
        (step_id, plan_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("这一步不存在。")
    return row


def _refresh_plan(plan_id: int) -> None:
    # 局部导入避免 action_plan_service ↔ action_step_service 循环依赖。
    from app.services.action_plan_service import refresh_plan_status
    refresh_plan_status(plan_id)


def list_steps(plan_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM action_plan_steps WHERE plan_id = ? ORDER BY position, id",
            (plan_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def create_steps_for_plan(conn, plan_id: int, steps, now: str) -> None:
    """把草案里的步骤**物化**成可执行行 ✓ —— `create_plan()` 调它 ✓（同一个事务 ✓）。"""
    for position, step in enumerate(steps):
        conn.execute(
            """
            INSERT INTO action_plan_steps (
                plan_id, position, title, minutes, priority,
                status, estimated_minutes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                plan_id,
                position,
                step.title,
                step.minutes,
                step.priority,
                step.minutes,
                now,
                now,
            ),
        )


def start_step(plan_id: int, step_id: int) -> dict:
    """开始一步 ✓ —— `pending` → `active` ✓。

    ⚠️ **这里不自动起番茄钟** ✗ —— 计时归 `pomodoro_service` 管 ✓，
    前端调它时把 `action_step_id` 带上 ✓（不要两套计时逻辑 ✗）。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = _step(conn, plan_id, step_id)
        if row["status"] == "completed":
            raise DomainValidationError("这一步已经完成了。")
        if row["status"] == "skipped":
            raise DomainValidationError("这一步已经跳过了。")
        conn.execute(
            "UPDATE action_plan_steps SET status = 'active', "
            "started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?",
            (now, now, step_id),
        )
        result = _row(_step(conn, plan_id, step_id))
    _refresh_plan(plan_id)
    return result


def pause_step(plan_id: int, step_id: int) -> dict:
    """暂停 ✓ —— `active` → `pending` ✓，**中断次数 +1** ✓。

    ⚠️ `interruption_count` 是 ⑥「最常见的中断原因」和 ⑦「计划偏差」的原料 ✓。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = _step(conn, plan_id, step_id)
        if row["status"] != "active":
            raise DomainValidationError("只有进行中的步骤才能暂停。")
        conn.execute(
            "UPDATE action_plan_steps SET status = 'pending', "
            "interruption_count = interruption_count + 1, updated_at = ? WHERE id = ?",
            (now, step_id),
        )
        result = _row(_step(conn, plan_id, step_id))
    _refresh_plan(plan_id)
    return result


def complete_step(plan_id: int, step_id: int) -> dict:
    """完成一步 ✓ —— 写完成时间 ✓，并**顺手完成它落地的 DDL / 日程** ✓。

    ⚠️ 完成是**幂等**的 ✓ —— 重复调用直接返回 ✓，不报错 ✓
    （前端双击 / 重试都不该炸 ✗）。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = _step(conn, plan_id, step_id)
        if row["status"] == "completed":
            return _row(row)
        conn.execute(
            "UPDATE action_plan_steps SET status = 'completed', "
            "completed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, step_id),
        )
        done = _row(_step(conn, plan_id, step_id))

    # ⚠️ **事务外**做 ✓ —— 这两个 service 会自己开连接 ✓，
    # 在 `with get_connection()` 里面调用它们会**嵌套事务** ✓（SQLite 会锁死 ✗）。
    if done["deadline_id"]:
        try:
            deadline_service.complete_deadline(done["deadline_id"])
        except Exception:  # noqa: BLE001 - 落地对象没了不该让"完成"失败
            logger.warning("完成步骤时，对应 DDL 不存在了：%s", done["deadline_id"])
    if done["schedule_id"]:
        try:
            schedule_service.complete_schedule(done["schedule_id"])
        except Exception:  # noqa: BLE001
            logger.warning("完成步骤时，对应日程不存在了：%s", done["schedule_id"])
    _refresh_plan(plan_id)
    return done


def skip_step(plan_id: int, step_id: int, reason: str | None = None) -> dict:
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = _step(conn, plan_id, step_id)
        if row["status"] == "completed":
            raise DomainValidationError("这一步已经完成了，不能跳过。")
        conn.execute(
            "UPDATE action_plan_steps SET status = 'skipped', skip_reason = ?, "
            "updated_at = ? WHERE id = ?",
            (reason, now, step_id),
        )
        result = _row(_step(conn, plan_id, step_id))
    _refresh_plan(plan_id)
    return result


def adjust_step(
    plan_id: int,
    step_id: int,
    *,
    title: str | None = None,
    minutes: int | None = None,
    priority: str | None = None,
    importance: int | None = None,
    difficulty: int | None = None,
) -> dict:
    """调整一步 ✓ —— **只改传进来的字段** ✓（`None` = 不改 ✓）。

    ⚠️ **重要 / 困难分开** ✗（2026-10-05 第三轮 ✓）：
    `importance` 是**价值** ✓，`difficulty` 是**执行负担** ✓ —— 两回事 ✓。
    传 **0** 表示"明确标记为不重要 / 不难" ✓（和"还没标记"= 不传 ✓ 不同 ✓）。
    """
    now = to_utc_iso(utc_now())
    fields: list[str] = []
    values: list = []

    if title is not None:
        fields.append("title = ?")
        values.append(title.strip())
    if minutes is not None:
        fields.append("minutes = ?")
        values.append(minutes)
        fields.append("estimated_minutes = ?")
        values.append(minutes)
    if priority is not None:
        fields.append("priority = ?")
        values.append(priority)
    if importance is not None:
        fields.append("importance = ?")
        values.append(importance)
    if difficulty is not None:
        fields.append("difficulty = ?")
        values.append(difficulty)

    with get_connection() as conn:
        _step(conn, plan_id, step_id)  # 不存在就 404 ✓
        if fields:
            fields.append("updated_at = ?")
            values.append(now)
            values.extend([step_id, plan_id])
            conn.execute(
                f"UPDATE action_plan_steps SET {', '.join(fields)} "
                "WHERE id = ? AND plan_id = ?",
                values,
            )
        return _row(_step(conn, plan_id, step_id))


def materialize_step(plan_id, step_id, *, kind="none", due_at=None,
                     start_at=None, end_at=None, materialize_key=None):
    import json
    from app.core.config import APP_TIMEZONE
    from app.core.time_utils import local_datetime_to_utc_iso
    payload = json.dumps([kind, due_at, start_at, end_at], ensure_ascii=False)
    now = to_utc_iso(utc_now())
    if kind not in {"none", "deadline", "schedule"}:
        raise DomainValidationError("未知落地类型。")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _step(conn, plan_id, step_id)
        if materialize_key:
            old = conn.execute("SELECT * FROM action_plan_steps WHERE materialize_key=?", (materialize_key,)).fetchone()
            if old:
                if old["id"] != step_id or old["materialize_payload"] != payload:
                    raise DomainValidationError("幂等键已使用或请求内容不同；旧版请求请刷新查看已有记录。")
                return _row(old)
        if row["deadline_id"] or row["schedule_id"]:
            raise DomainValidationError("这一步已经落地过了。")
        if kind == "none":
            return _row(row)
        try:
            if kind == "deadline":
                if not due_at:
                    raise ValueError("缺少截止时间")
                due = local_datetime_to_utc_iso(due_at, APP_TIMEZONE)
                cur = conn.execute("""INSERT INTO deadlines
                    (title,due_at,created_at,updated_at) VALUES (?,?,?,?)""",
                    (row["title"], due, now, now))
                column = "deadline_id"
            else:
                if not start_at:
                    raise ValueError("缺少开始时间")
                start, end = schedule_service._convert_times(start_at, end_at)
                cur = conn.execute("""INSERT INTO schedule_events
                    (title,start_at,end_at,source_type,created_at,updated_at)
                    VALUES (?,?,?,'action_plan',?,?)""", (row["title"],start,end,now,now))
                column = "schedule_id"
        except (ValueError, TypeError) as exc:
            raise DomainValidationError("落地时间无效。") from exc
        conn.execute(f"UPDATE action_plan_steps SET {column}=?, materialize_key=?, materialize_payload=?, updated_at=? WHERE id=?",
                     (cur.lastrowid,materialize_key,payload,now,step_id))
        return _row(_step(conn,plan_id,step_id))


def record_pomodoro(step_id: int, minutes: int) -> dict:
    """番茄钟结束时叫它 ✓（⑤）—— 把**实际耗时**写回步骤 ✓。

    ⚠️ 用 `+=` 不是覆盖 ✓ —— 一步可以跑**好几个**番茄钟 ✓，
    实际耗时是它们的**和** ✓（"预计 50 分钟、实际 70" 才有意义 ✓）。
    """
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM action_plan_steps WHERE id = ?", (step_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("这一步不存在。")
        conn.execute(
            "UPDATE action_plan_steps SET actual_minutes = actual_minutes + ?, "
            "pomodoro_count = pomodoro_count + 1, updated_at = ? WHERE id = ?",
            (int(minutes), now, step_id),
        )
        return _row(
            conn.execute(
                "SELECT * FROM action_plan_steps WHERE id = ?", (step_id,)
            ).fetchone()
        )
