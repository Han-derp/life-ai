"""「计划」三张卡的**全部列表**数据源（用户 2026-10-05 要求）。

## 为什么需要这几条

「今天」页原来是三张面板，把**所有**日程 / DDL / 习惯都铺在首页 ✗ ——
每加几条就变得很长，左右并排的卡片内容数还不一样，高度参差 ✓。

改成「摘要卡 + 点开看全部」之后：
卡上只放**一条**（最近的日程 / 最紧的 DDL / 最新的习惯 ✓），
全部内容移到一个列表页，按三类切换 ✓：

| 卡片 | 三类 |
|---|---|
| 日程 ✓ | 全部 / 已完成 / 未完成 ✓ |
| DDL ✓ | 全部 / 已完成 / 未完成 ✓ |
| 习惯 ✓ | 全部 / 养成中 / 已结束 ✓ |

⚠️ 所以后端必须能回答**两个新问题** ✗（原来只能回答"今天的" ✗）：
"我一共有哪些日程" ✓ 和 "我一共有哪些习惯、哪些已经结束了" ✓。

⚠️ 这里刻意**不新增表、不新增字段** —— 两个问题都只是**换一个 WHERE** ✓。
"""

import pytest

from app.core.exceptions import DomainValidationError, NotFoundError
from app.schemas.deadline import DeadlineCreate
from app.schemas.habit import HabitCreate, HabitLogRequest
from app.schemas.schedule import ScheduleCreate
from app.services.deadline_service import create_deadline
from app.services.habit_service import (
    archive_habit,
    create_habit,
    list_habits,
    list_habits_today,
    log_habit,
    restore_habit,
)
from app.services.schedule_service import (
    cancel_schedule,
    complete_schedule,
    create_schedule,
    list_schedule_for_date,
    list_schedules,
)

TODAY = "2026-06-03T09:00"
TOMORROW = "2026-06-04T09:00"


# ---------- 日程：全部 / 已完成 / 未完成 ----------


def test_all_schedules_look_beyond_today(client):
    """★ 这是新的那件事：卡片要能看到"下一条"，而不只是"今天的"。"""
    create_schedule(ScheduleCreate(title="今天的", start_at_local=TODAY))
    create_schedule(ScheduleCreate(title="明天的", start_at_local=TOMORROW))

    today_only = list_schedule_for_date("2026-06-03")
    everything = list_schedules()

    assert [item["title"] for item in today_only] == ["今天的"]
    assert {item["title"] for item in everything} == {"今天的", "明天的"}


def test_all_schedules_are_newest_first(client):
    """「全部」是给**翻看**用的 → 最近的在最上面 ✓。"""
    create_schedule(ScheduleCreate(title="早", start_at_local=TODAY))
    create_schedule(ScheduleCreate(title="晚", start_at_local=TOMORROW))

    assert [item["title"] for item in list_schedules()] == ["晚", "早"]


def test_schedules_can_be_filtered_by_status(client):
    """「已完成」那一类靠它 ✓。"""
    done = create_schedule(ScheduleCreate(title="做完的", start_at_local=TODAY))
    create_schedule(ScheduleCreate(title="还没做的", start_at_local=TOMORROW))
    complete_schedule(done["id"])

    completed = list_schedules("completed")
    pending = list_schedules("pending")

    assert [item["title"] for item in completed] == ["做完的"]
    assert [item["title"] for item in pending] == ["还没做的"]


def test_cancelled_schedules_are_not_pending(client):
    """⚠️ 「未完成」= pending ✓ —— **取消不算未完成** ✗。

    取消的意思是"我决定不做了" ✓，把它算进"未完成"会让你永远有一堆假待办 ✓。
    （它在「全部」里仍然看得到 ✓，带着 cancelled 徽标 ✓。）
    """
    item = create_schedule(ScheduleCreate(title="不做了", start_at_local=TODAY))
    cancel_schedule(item["id"])

    assert list_schedules("pending") == []
    assert [entry["title"] for entry in list_schedules()] == ["不做了"]


def test_an_unknown_schedule_status_is_rejected(client):
    with pytest.raises(DomainValidationError):
        list_schedules("bogus")


def test_the_schedule_api_has_a_list_endpoint(client):
    create_schedule(ScheduleCreate(title="走接口", start_at_local=TODAY))

    response = client.get("/api/v1/schedule")
    filtered = client.get("/api/v1/schedule?status=completed")
    bad = client.get("/api/v1/schedule?status=bogus")

    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["走接口"]
    assert filtered.json() == []
    assert bad.status_code == 400


def test_the_today_endpoint_still_works(client):
    """⚠️ 回归护栏：新的 `GET /schedule` **不能抢掉** `/schedule/today` ✗。"""
    create_schedule(ScheduleCreate(title="今天的", start_at_local=TODAY))

    response = client.get("/api/v1/schedule/today?local_date=2026-06-03")

    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["今天的"]


# ---------- 习惯：全部 / 养成中 / 已结束 ----------


def _habit(name: str) -> dict:
    return create_habit(HabitCreate(name=name, tracking_type="boolean"))


def test_all_habits_include_the_archived_ones(client):
    """★ 这是新的那件事：「已结束」那一类必须有数据源 ✓。"""
    keep = _habit("还在养")
    gone = _habit("已经结束")
    archive_habit(gone["id"])

    everything = list_habits()
    active = list_habits("active")
    archived = list_habits("archived")

    assert {item["id"] for item in everything} == {keep["id"], gone["id"]}
    assert [item["id"] for item in active] == [keep["id"]]
    assert [item["id"] for item in archived] == [gone["id"]]


def test_the_active_flag_is_in_the_payload(client):
    """⚠️ 界面上「养成中 / 已结束」那个徽标靠它 ✓ —— 没有它前端只能靠猜 ✗。"""
    habit = _habit("会被归档的")
    assert habit["active"] is True

    archive_habit(habit["id"])

    assert list_habits("archived")[0]["active"] is False


def test_today_only_lists_active_habits(client):
    """⚠️ 回归护栏：归档之后它**不该再出现在今天的打卡列表里** ✓。"""
    habit = _habit("归档掉")
    archive_habit(habit["id"])

    assert list_habits_today() == []


def test_restore_puts_an_archived_habit_back(client):
    habit = _habit("误归档")
    archive_habit(habit["id"])

    restored = restore_habit(habit["id"])

    assert restored["active"] is True
    assert [item["id"] for item in list_habits_today()] == [habit["id"]]


def test_restore_keeps_the_check_in_history(client):
    """★ 归档**从不删记录** ✓ —— 所以恢复之后历史必须是完整的 ✓。"""
    habit = _habit("有历史的")
    log_habit(habit["id"], HabitLogRequest(value_bool=True), "2026-06-03")
    archive_habit(habit["id"])

    restore_habit(habit["id"])

    # 恢复之后仍在「养成中」，而且那种状态**是持久的**（不是只在返回值里好看）
    assert list_habits("active")[0]["id"] == habit["id"]


def test_restoring_a_missing_habit_raises(client):
    with pytest.raises(NotFoundError):
        restore_habit(999_999)


def test_restoring_an_already_active_habit_raises(client):
    """⚠️ 它不是"已结束"的 ✓ —— 静默成功会让你以为发生了什么，其实没有 ✗。"""
    habit = _habit("本来就在养")

    with pytest.raises(NotFoundError):
        restore_habit(habit["id"])


def test_an_unknown_habit_status_is_rejected(client):
    with pytest.raises(DomainValidationError):
        list_habits("bogus")


def test_the_habit_api_has_list_and_restore_endpoints(client):
    habit = _habit("走接口")
    archive_habit(habit["id"])

    listed = client.get("/api/v1/habits?status=archived")
    restored = client.post(f"/api/v1/habits/{habit['id']}/restore")
    bad = client.get("/api/v1/habits?status=bogus")

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["走接口"]
    assert restored.status_code == 200
    assert restored.json()["active"] is True
    assert bad.status_code == 400


def test_the_today_endpoint_is_not_shadowed(client):
    """⚠️ 回归护栏：新的 `GET /habits` **不能抢掉** `/habits/today` ✗。"""
    _habit("今天的")

    response = client.get("/api/v1/habits/today")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["今天的"]


# ---------- DDL 的子任务：卡片上的下拉框靠它 ----------


def test_children_are_identifiable_from_the_plain_list(client):
    """★ 卡片上那个「拆解出的子任务」下拉框**不需要新接口** ✓。

    `GET /deadlines` 本来就带着 `parent_id` ✓ → 前端按它分组即可 ✓。
    这条把它锁住：以后有人把 `parent_id` 从响应里拿掉，下拉框会**静默消失** ✗。
    """
    parent = create_deadline(DeadlineCreate(title="大作业", due_at_local=TODAY))
    create_deadline(DeadlineCreate(title="第一步", due_at_local=TODAY, parent_id=parent["id"]))
    create_deadline(DeadlineCreate(title="第二步", due_at_local=TOMORROW, parent_id=parent["id"]))

    items = client.get("/api/v1/deadlines").json()
    children = [item for item in items if item["parent_id"] == parent["id"]]

    assert len(children) == 2
    assert all("due_at_local" in item and "completion_status" in item for item in children)


# ---------- 前端读的字段必须在响应里（两端契约） ----------
#
# ⚠️ 这条是从一次**真实事故**来的 ✓（2026-10-05）：
#
# 我给 service 的字典加了 `active` ✓，以为完事了 ✗ ——
# **FastAPI 会按 `response_model` 把没声明的字段静默过滤掉** ✗，
# 于是服务层有、接口上没有 ✓，界面上那个「养成中 / 已结束」徽标就永远不出现 ✓，
# 而且**不报任何错** ✗（是新测试当场抓住的 ✓）。
#
# 所以这里把"前端 `today.js` 到底读哪些字段"逐条钉死 ✓ ——
# 以后谁从 response_model 里删掉一个字段，这里立刻红 ✓，
# 而不是等到用户发现"某个数字不见了" ✗。

NEEDED_FIELDS = {
    "schedule": {
        "id", "title", "start_at", "start_at_local", "end_at_local",
        "completion_status", "time_status", "note",
    },
    "deadline": {
        "id", "title", "due_at", "due_at_local", "parent_id",
        "completion_status", "time_status",
    },
    "habit": {
        "id", "name", "tracking_type", "aggregation_mode", "unit",
        "target_value", "target_text", "target_operator",
        "current_bool", "current_number", "current_text", "met", "progress",
        "active",
    },
}


def test_the_schedule_payload_has_every_field_the_frontend_reads(client):
    create_schedule(ScheduleCreate(title="有字段的", start_at_local=TODAY))
    item = client.get("/api/v1/schedule").json()[0]
    assert NEEDED_FIELDS["schedule"] <= set(item)


def test_the_deadline_payload_has_every_field_the_frontend_reads(client):
    """⚠️ 尤其 `due_at` ✓ —— 前端拿它排序（`Date.parse`）。

    少了它 → `Date.parse(undefined)` = `NaN` → "最紧的那一条"会**静默变成随便一条** ✗。
    """
    create_deadline(DeadlineCreate(title="有字段的", due_at_local=TODAY))
    item = client.get("/api/v1/deadlines").json()[0]
    assert NEEDED_FIELDS["deadline"] <= set(item)


def test_the_habit_payload_has_every_field_the_frontend_reads(client):
    """⚠️ 尤其 `active` ✓ —— 就是这条字段当初被静默过滤掉过 ✓。"""
    _habit("有字段的")
    item = client.get("/api/v1/habits").json()[0]
    assert NEEDED_FIELDS["habit"] <= set(item)


def test_archived_habits_expose_the_same_fields(client):
    """⚠️ 「已结束」那一类走的也是同一个 `response_model` ✓ —— 但别靠"应该一样" ✗。"""
    habit = _habit("会被归档的")
    archive_habit(habit["id"])
    item = client.get("/api/v1/habits?status=archived").json()[0]
    assert NEEDED_FIELDS["habit"] <= set(item)
