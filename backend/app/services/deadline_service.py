import json
import re

from pydantic import ValidationError

from app.core.config import APP_TIMEZONE
from app.core.exceptions import DomainValidationError, ExternalServiceError, NotFoundError
from app.core.prompt_loader import load_prompt
from app.core.time_utils import local_datetime_to_utc_iso, to_utc_iso, utc_now, utc_to_local_iso
from app.database.connection import get_connection
from app.llm import factory
from app.schemas.deadline import (
    DeadlineBreakdownAIResult,
    DeadlineBreakdownApply,
    DeadlineCreate,
    DeadlineUpdate,
)
from app.services.status_utils import deadline_time_status


# ⚠️ §1.1 那张表里**没有"同步结构化抽取"这一档**。
# 这里用 `chat`（= 云端）而不是 `extract`，因为它是**同步的**：你点一下就在等结果。
# `extract` 走本地的理由里明确有一条"且异步，60 秒无所谓"，而拆解 DDL 不异步。
# 这一处需要设计窗口裁决 —— 见 TODO.md 的 C7。
_provider = factory.current_provider("deadline_breakdown")



def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "due_at": row["due_at"],
        "due_at_local": utc_to_local_iso(row["due_at"], APP_TIMEZONE),
        "parent_id": row["parent_id"],
        "completion_status": row["completion_status"],
        "time_status": deadline_time_status(row["due_at"], row["completion_status"]),
        "priority": row["priority"],
        "sort_order": row["sort_order"],
    }


def _validate_parent(conn, parent_id: int | None) -> None:
    if parent_id is None:
        return
    parent = conn.execute(
        "SELECT id, parent_id FROM deadlines WHERE id = ?", (parent_id,)
    ).fetchone()
    if parent is None:
        raise DomainValidationError("父 DDL 不存在。")
    if parent["parent_id"] is not None:
        raise DomainValidationError("V1 只允许一层 milestone。")


def create_deadline(data: DeadlineCreate) -> dict:
    now = to_utc_iso(utc_now())
    try:
        due_at = local_datetime_to_utc_iso(data.due_at_local, APP_TIMEZONE)
    except ValueError as exc:
        raise DomainValidationError("截止时间格式无效。") from exc

    with get_connection() as conn:
        _validate_parent(conn, data.parent_id)
        cursor = conn.execute(
            """
            INSERT INTO deadlines (
                title, description, due_at, parent_id, completion_status,
                priority, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, 0, ?, ?)
            """,
            (
                data.title.strip(),
                data.description.strip() if data.description else None,
                due_at,
                data.parent_id,
                data.priority,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def list_deadlines(status: str | None = None) -> list[dict]:
    query = "SELECT * FROM deadlines"
    params: tuple = ()
    if status:
        if status not in {"pending", "completed", "cancelled"}:
            raise DomainValidationError("无效的 DDL 状态。")
        query += " WHERE completion_status = ?"
        params = (status,)
    query += """
        ORDER BY
            CASE completion_status
                WHEN 'pending' THEN 0
                WHEN 'completed' THEN 1
                WHEN 'cancelled' THEN 2
            END,
            due_at ASC,
            priority DESC,
            id ASC
    """
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_deadline(deadline_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    if row is None:
        raise NotFoundError("DDL 不存在。")
    return _row_to_dict(row)


def update_deadline(deadline_id: int, data: DeadlineUpdate) -> dict:
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
        if row is None:
            raise NotFoundError("DDL 不存在。")
        if row["completion_status"] != "pending":
            raise DomainValidationError("只有 pending DDL 可以编辑。")

        title = data.title.strip() if data.title is not None else row["title"]
        description = data.description if data.description is not None else row["description"]
        due_at = row["due_at"]
        if data.due_at_local is not None:
            try:
                due_at = local_datetime_to_utc_iso(data.due_at_local, APP_TIMEZONE)
            except ValueError as exc:
                raise DomainValidationError("截止时间格式无效。") from exc
        priority = data.priority if data.priority is not None else row["priority"]

        conn.execute(
            """
            UPDATE deadlines
            SET title = ?, description = ?, due_at = ?, priority = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, description, due_at, priority, now, deadline_id),
        )
        updated = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    return _row_to_dict(updated)


def _set_status(deadline_id: int, status: str) -> dict:
    now = to_utc_iso(utc_now())
    timestamp_field = "completed_at" if status == "completed" else "cancelled_at"
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
        if row is None:
            raise NotFoundError("DDL 不存在。")
        if row["completion_status"] != "pending":
            raise DomainValidationError("只有 pending DDL 可以变更状态。")
        conn.execute(
            f"""
            UPDATE deadlines
            SET completion_status = ?, {timestamp_field} = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, now, deadline_id),
        )
        updated = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    return _row_to_dict(updated)


def complete_deadline(deadline_id: int) -> dict:
    return _set_status(deadline_id, "completed")


def cancel_deadline(deadline_id: int) -> dict:
    return _set_status(deadline_id, "cancelled")


def remove_deadline(deadline_id: int) -> dict:
    """**真的从库里删掉**它（连同它的子任务）。

    ## 和 `cancel_deadline()` 是两件事

    用户 2026-10-05 明确要求分开 ✓：

    | | 行还在吗 | 意思是 |
    |---|---|---|
    | **取消** ✓ | **在** ✓（只改 `completion_status`） | "我决定不做了，但这事发生过" ✓ |
    | **删除** ✓ | **不在** ✓ | "当我没记过这件事" ✓ |

    ⚠️ **子任务会一起删** —— 拆解出来的里程碑是挂在父任务下面的 ✓，
    删父留子在界面上就是一堆孤儿 ✗。
    （外键约束**不会**替我们做这件事 ✗：`parent_id` 没有 `ON DELETE CASCADE` ✓。）

    ⚠️ 提醒：这是**真的**没了 ✓ —— 想要留痕就用「取消」✓。
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM deadlines WHERE id = ?", (deadline_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("DDL 不存在。")

        children = conn.execute(
            "SELECT COUNT(*) AS n FROM deadlines WHERE parent_id = ?",
            (deadline_id,),
        ).fetchone()["n"]

        # 先子后父（顺序不能反 ✓）
        conn.execute("DELETE FROM deadlines WHERE parent_id = ?", (deadline_id,))
        conn.execute("DELETE FROM deadlines WHERE id = ?", (deadline_id,))

    return {"deleted_children": children}


def _extract_json(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("模型输出中没有 JSON 对象。")
    return cleaned[start : end + 1]


def _ask_breakdown_ai(parent: dict) -> DeadlineBreakdownAIResult:
    prompt = load_prompt("deadline_breakdown.md")
    payload = {
        "title": parent["title"],
        "description": parent["description"],
        "due_at_local": parent["due_at_local"],
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    raw = _provider.chat(messages, temperature=0.4)

    for attempt in range(2):
        try:
            parsed = json.loads(_extract_json(raw))
            return DeadlineBreakdownAIResult.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if attempt == 1:
                raise
            raw = _provider.chat(
                [
                    {
                        "role": "system",
                        "content": "只修复下面内容为符合给定结构的严格JSON；不要添加新事实。",
                    },
                    {
                        "role": "user",
                        "content": f"校验错误：{exc}\n原输出：\n{raw}\n\n目标结构：\n{prompt}",
                    },
                ],
                temperature=0.2,
            )
    raise RuntimeError("unreachable")


def suggest_deadline_breakdown(deadline_id: int) -> dict:
    """AI Read + Suggest only. Nothing is written to the database."""
    parent = get_deadline(deadline_id)
    if parent["parent_id"] is not None:
        raise DomainValidationError("V1 只允许一层 milestone，不能对子任务再拆解。")

    if not _provider.available:
        return {"deadline_id": deadline_id, "subtasks": [], "ai_available": False}

    try:
        result = _ask_breakdown_ai(parent)
    except (ExternalServiceError, ValidationError, ValueError, json.JSONDecodeError):
        return {"deadline_id": deadline_id, "subtasks": [], "ai_available": False}

    return {
        "deadline_id": deadline_id,
        "subtasks": [item.model_dump() for item in result.subtasks],
        "ai_available": True,
    }


def apply_deadline_breakdown(deadline_id: int, data: DeadlineBreakdownApply) -> list[dict]:
    """User-confirmed write: create child deadlines under `deadline_id` in one transaction."""
    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        parent = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
        if parent is None:
            raise NotFoundError("DDL 不存在。")
        if parent["parent_id"] is not None:
            raise DomainValidationError("V1 只允许一层 milestone，不能对子任务再拆解。")

        created_rows = []
        for subtask in data.subtasks:
            if subtask.due_at_local is not None:
                try:
                    due_at = local_datetime_to_utc_iso(subtask.due_at_local, APP_TIMEZONE)
                except ValueError as exc:
                    raise DomainValidationError("子任务截止时间格式无效。") from exc
            else:
                due_at = parent["due_at"]

            cursor = conn.execute(
                """
                INSERT INTO deadlines (
                    title, description, due_at, parent_id, completion_status,
                    priority, sort_order, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, 'pending', ?, 0, ?, ?)
                """,
                (
                    subtask.title.strip(),
                    due_at,
                    deadline_id,
                    subtask.priority,
                    now,
                    now,
                ),
            )
            created_rows.append(
                conn.execute("SELECT * FROM deadlines WHERE id = ?", (cursor.lastrowid,)).fetchone()
            )

    return [_row_to_dict(row) for row in created_rows]

