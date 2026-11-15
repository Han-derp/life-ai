"""可注入的时钟（DEVELOPMENT_PLAN v2.0 · S8）。

主动联系的全部判断都依赖"现在"，所以**"现在"必须能被替换**——
否则"沉默 3 天""满 1 个月"这类规则只能靠真的等三天来验证，
而方案明确要求"用假时钟测试，不要等 3 天"。

工作流里的函数都接受 ``now`` 参数；本模块只提供默认值和时区换算。
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import APP_TIMEZONE
from app.core.time_utils import utc_now


def now_utc() -> datetime:
    return utc_now()


def to_local(moment: datetime) -> datetime:
    return moment.astimezone(ZoneInfo(APP_TIMEZONE))


def local_date(moment: datetime) -> date:
    return to_local(moment).date()
