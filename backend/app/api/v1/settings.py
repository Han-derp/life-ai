from fastapi import APIRouter, Response

from app.api import auth
from app.schemas.settings import (
    SettingsResponse,
    SettingsTestResponse,
    SettingsUpdate,
)
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
def get_settings():
    """当前配置。**不会返回 API Key 全文**，只有尾 4 位。"""
    return settings_service.status()


@router.post("", response_model=SettingsResponse)
def post_settings(data: SettingsUpdate, response: Response):
    """保存 API Key / 模型 / 三档 / **访问密码**。

    **Key 写进 `backend/.env`，不落数据库。**
    现在**全部立刻生效**（消费方持有的是 provider 代理 ✓，
    `auth` 也每次请求都读环境变量 ✓）—— 只有改 Python 代码才要重启 ✓。
    """
    result = settings_service.save(
        api_key=data.api_key,
        model=data.model,
        tiers=data.tiers,
        app_password=data.app_password,
    )

    # ★ 设了新密码 → **顺手把当前这台浏览器登录上** ✓。
    # 不做这一步的话：密码一存上，这个页面**下一次请求就被自己拦成 401** ✗
    # —— 用户会以为"填了个密码把应用弄坏了" ✓。
    if data.app_password:
        response.set_cookie(
            auth.COOKIE_NAME,
            auth.token(),
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
            path="/",
        )
    elif data.app_password is not None:
        # 清空了密码 → 顺手把 Cookie 也清掉 ✓
        response.delete_cookie(auth.COOKIE_NAME, path="/")

    return result


@router.post("/test", response_model=SettingsTestResponse)
def post_settings_test():
    """真的发一次最小请求，确认 Key 与模型名都可用。

    这是唯一能确认"不再报模型不存在"的方法。会消耗极少量 token。
    """
    return settings_service.test_connection()
