from fastapi import APIRouter, Query

from app.schemas.baseline import BaselineAlertResponse
from app.schemas.proactive import ProactiveContactResponse, ProactiveTickResponse
from app.workflows import baseline, proactive

router = APIRouter(prefix="/proactive", tags=["proactive"])


@router.get("/contacts", response_model=list[ProactiveContactResponse])
def get_contacts(limit: int = Query(default=100, ge=1, le=500)):
    """她主动说过的所有话（含还没给你看的小作文）。"""
    return proactive.list_contacts(limit)


@router.get("/pending", response_model=list[ProactiveContactResponse])
def get_pending():
    """还没给你看过的东西——机制二的小作文，**你回来时才展示**。"""
    return proactive.pending_contacts()


@router.post("/contacts/{contact_id}/deliver", response_model=ProactiveContactResponse)
def post_deliver(contact_id: int):
    """标成"已经给你看过了"。幂等。"""
    return proactive.deliver_contact(contact_id)


@router.post("/tick", response_model=ProactiveTickResponse)
def post_tick():
    """手动走一次主动联系。

    加这个端点是为了**不用等三天**就能验证 S8 的规则：
    把数据库弄成"三天前说过话"的样子，再调它就行。
    顺手也会检测一次基线偏离（和后台调度器做的事一样）。

    真正的自动触发在后台调度器里（`app/scheduler/runner.py`），
    它只负责按间隔叫一次同一个流程。
    """
    baseline.scan()
    return {"did": proactive.tick()}


# ---------- B 类基线预警（S8 后半） ----------


@router.get("/baseline", response_model=list[BaselineAlertResponse])
def get_baseline_alerts(
    include_dismissed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
):
    """检测到的基线偏离。

    她**只在两件事上**可以主动开口说「我注意到……怎么了？」：
    作息明显变晚、DDL 快到期却没进展。而且都要求**连续 3 天以上偏离常态**——
    单独一天熬夜不会被说，因为那不是"偏离"，那只是某一天。
    """
    return baseline.list_alerts(include_dismissed, limit)


@router.post("/baseline/scan", response_model=list[BaselineAlertResponse])
def post_baseline_scan():
    """立刻检测一次，不用等后台调度。"""
    return baseline.scan()


@router.post("/baseline/{alert_id}/voice", response_model=BaselineAlertResponse)
def post_baseline_voice(alert_id: int):
    """把它说出口。第一句永远是「我注意到……怎么了？」（方案 §5.1-B）"""
    return baseline.voice_alert(alert_id)


@router.post("/baseline/{alert_id}/dismiss", response_model=BaselineAlertResponse)
def post_baseline_dismiss(alert_id: int):
    """处理掉 / 忽略掉这条。之后同一段偏离可以重新被检测出来。"""
    return baseline.dismiss_alert(alert_id)
