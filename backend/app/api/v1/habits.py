from fastapi import APIRouter, Query, Response

from app.schemas.habit import HabitCreate, HabitLogRequest, HabitTodayResponse
from app.services.habit_service import (
    archive_habit,
    create_habit,
    list_habits,
    list_habits_today,
    log_habit,
    remove_habit,
    restore_habit,
)

router = APIRouter(prefix="/habits", tags=["habits"])


@router.get("/today", response_model=list[HabitTodayResponse])
def get_today_habits(local_date: str | None = Query(default=None)):
    return list_habits_today(local_date)


@router.get("", response_model=list[HabitTodayResponse])
def get_habits(status: str | None = Query(default=None), local_date: str | None = Query(default=None)):
    """**全部**习惯（给「查看全部」那个列表页用）。

    `status` 可省略 ✓（= 全部 ✓），或 `active`（养成中）/ `archived`（已结束）✓。
    """
    return list_habits(status, local_date)


@router.post("", response_model=HabitTodayResponse, status_code=201)
def post_habit(data: HabitCreate):
    return create_habit(data)


@router.post("/{habit_id}/log", response_model=HabitTodayResponse)
def post_habit_log(habit_id: int, data: HabitLogRequest, local_date: str | None = Query(default=None)):
    return log_habit(habit_id, data, local_date)


@router.post("/{habit_id}/restore", response_model=HabitTodayResponse)
def post_restore_habit(habit_id: int):
    """把**已结束**的习惯放回「养成中」。

    ⚠️ 「归档」的反操作 ✓ —— 没有它，归档就是单向的 ✗
    （点错一次就只能去改数据库 ✓）。打卡记录一直都在 ✓，所以恢复是完整的 ✓。
    """
    return restore_habit(habit_id)


@router.delete("/{habit_id}", status_code=204)
def delete_habit(habit_id: int):
    archive_habit(habit_id)
    return Response(status_code=204)


@router.delete("/{habit_id}/purge")
def purge_habit(habit_id: int):
    """**彻底删除**这个习惯，连同它**全部打卡记录**。

    ⚠️ 和上面那个 `DELETE /{habit_id}`（= **归档** ✓）是两件事：

    - 上面那个 ✓：`active = 0` ✓，**打卡记录全留着** ✓
    - 这个 ✗：习惯行 + **它的全部打卡记录**一起删 ✓，**不可恢复** ✗

    （路由叫 `purge` 是为了不和"归档"抢同一个路径 ✓ ——
    `DELETE /habits/{id}` 的语义历史上已经是归档了 ✓，改它会打破界面 ✓。）

    返回 `{"deleted_logs": n}` ✓ —— 界面用它告诉你一并删掉了几条记录 ✓。
    """
    return remove_habit(habit_id)
