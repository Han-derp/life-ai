"""彻底删除习惯 —— 用户 2026-10-05 要求：

> 「习惯归档按钮再加一个彻底删除按钮，连之前记录所有条目都删除」

⚠️ 和「归档」是**两件事** ✗：

| | 习惯行 | 打卡记录 |
|---|---|---|
| **归档** ✓ | 还在（`active = 0`）✓ | **全留着** ✓ |
| **彻底删除** ✓ | 没了 ✗ | **一起删掉** ✗ |
"""

import pytest

from app.core.exceptions import NotFoundError
from app.database.connection import get_connection
from app.schemas.habit import HabitCreate, HabitLogRequest
from app.services.habit_service import (
    archive_habit,
    create_habit,
    list_habits_today,
    log_habit,
    remove_habit,
)


def _habit_with_logs(name: str) -> dict:
    habit = create_habit(HabitCreate(name=name, tracking_type="boolean"))
    log_habit(habit["id"], HabitLogRequest(value_bool=True), local_date="2026-06-01")
    log_habit(habit["id"], HabitLogRequest(value_bool=True), local_date="2026-06-02")
    return habit


def _log_count(habit_id: int) -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM habit_logs WHERE habit_id = ?", (habit_id,)
        ).fetchone()["n"]


def test_purge_removes_the_habit(client):
    habit = _habit_with_logs("喝水")

    remove_habit(habit["id"])

    names = [item["name"] for item in list_habits_today("2026-06-03")]
    assert "喝水" not in names


def test_purge_also_removes_all_its_logs(client):
    """★ **这条就是用户要的东西**：连之前记录的**所有条目**都删掉 ✓。"""
    habit = _habit_with_logs("喝水")

    result = remove_habit(habit["id"])

    assert result["deleted_logs"] == 2
    assert _log_count(habit["id"]) == 0


def test_purge_does_not_touch_other_habits(client):
    _habit_with_logs("留着")
    doomed = _habit_with_logs("删掉")

    remove_habit(doomed["id"])

    names = [item["name"] for item in list_habits_today("2026-06-03")]
    assert names == ["留着"]


def test_archive_keeps_the_logs_but_purge_does_not(client):
    """⚠️ **这条是这次改动的核心** ✓：归档留着记录 ✓，彻底删除不留 ✗。"""
    archived = _habit_with_logs("归档的")
    purged = _habit_with_logs("删掉的")

    archive_habit(archived["id"])
    remove_habit(purged["id"])

    assert _log_count(archived["id"]) == 2  # 归档：记录**还在** ✓
    assert _log_count(purged["id"]) == 0  # 删除：记录**没了** ✗


def test_purging_a_habit_without_logs_is_fine(client):
    """没打过卡的习惯也能删 ✓（别因为 0 条记录就报错 ✗）。"""
    habit = create_habit(HabitCreate(name="还没打过卡", tracking_type="boolean"))

    assert remove_habit(habit["id"])["deleted_logs"] == 0


def test_purging_a_missing_habit_raises(client):
    with pytest.raises(NotFoundError):
        remove_habit(999_999)


def test_the_api_has_a_purge_endpoint(client):
    habit = _habit_with_logs("走接口删")

    response = client.delete(f"/api/v1/habits/{habit['id']}/purge")

    assert response.status_code == 200
    assert response.json()["deleted_logs"] == 2
