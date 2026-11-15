from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.error_handlers import register_error_handlers
from app.api import auth
from app.api.v1.router import api_router
from app.core.config import (
    APP_TIMEZONE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    PROACTIVE_ENABLED,
    PROACTIVE_TICK_SECONDS,
)
from app.database.bootstrap import bootstrap_database
from app.scheduler.runner import start_scheduler, stop_scheduler
from app.services.image_service import IMAGES_DIR
from app.services.memory_service import backfill_search_index


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap_database()

    # 记忆检索索引（FTS5）：migration 011 建表**之前**就存在的观察与判断
    # 要补进来 —— 分词在 Python 里做，SQL 补不了。幂等，重复启动没有代价。
    backfill_search_index()

    # S8：主动联系的后台调度。
    # 已知限制（方案 §6 接受）：**应用关着时不发**。
    if PROACTIVE_ENABLED:
        start_scheduler(PROACTIVE_TICK_SECONDS)

    yield

    stop_scheduler()


class UTF8JSONResponse(JSONResponse):
    """**明确声明 UTF-8。**

    JSON 规范（RFC 8259）说默认就是 UTF-8 ✓，所以严格讲不写也对 ——
    但老客户端（Windows PowerShell 5.1、某些桌面 app）看到
    `application/json` 上没有 charset，就按 Latin-1 解 ✗ → 中文全成乱码。
    它们是错的，但**我们能让它们对** —— 加两个词而已 ✓。

    实测：Windows PowerShell 等客户端可能按错误字符集解码中文 ——
    `ä½ æ¯å¯¹é£ä¸ª…` ✓。
    """

    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="Life AI",
    version="1.0.0",
    lifespan=lifespan,
    # 全站所有 JSON 都带上 charset=utf-8 ✓（第三方客户端最容易栽在这里）
    default_response_class=UTF8JSONResponse,
)
register_error_handlers(app)
app.include_router(api_router)

# ── 访问密码（可选）───────────────────────────────────────────────
#
# 默认**不启用**（`APP_PASSWORD` 为空 ✓）—— 本机用 `127.0.0.1` 时完全不受影响 ✓。
# 设了密码之后，**所有**请求都要过（唯一例外是登录本身 ✓）。
#
# ⚠️ `/login` 必须在**静态挂载之前**注册 —— 静态挂载在 "/" 上，
# 会把任何没被更早匹配到的路径吃掉 ✓（和上面那条注释同一个道理 ✓）。
app.include_router(auth.router)


@app.get("/login", include_in_schema=False)
def login_page():
    return HTMLResponse(auth.LOGIN_PAGE)


@app.middleware("http")
async def require_password(request, call_next):
    """设了 `APP_PASSWORD` 时，所有请求都要过。"""
    if not auth.enabled() or auth.is_public(request.url.path):
        return await call_next(request)
    if auth.authorized(request):
        return await call_next(request)
    return auth.reject(request)


@app.get("/api/v1/health", tags=["system"])
def health():
    return {
        "status": "ok",
        "timezone": APP_TIMEZONE,
        "ai_configured": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL,
        "proactive_enabled": PROACTIVE_ENABLED,
    }


FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# 聊天图片存在本地文件里（不进数据库 —— 原因见 image_service 的说明）。
# 目录必须先建出来，否则 StaticFiles 直接抛错、整个应用起不来。
# ⚠️ 必须在 "/" 之前挂载：那个挂载会把任何没被更早匹配到的路径吃掉。
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

if FRONTEND_DIR.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )
