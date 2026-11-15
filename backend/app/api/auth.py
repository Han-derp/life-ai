"""访问密码 —— 让手机能连进来时不是"裸奔"。

## 默认不启用 ✓

`APP_PASSWORD` 为空 = **不启用** ✓ —— 本机用 `127.0.0.1` 时完全不需要它 ✓，
所以这个功能不会打扰原来的用法 ✓。

## 为什么需要它

`NOTES.md` / `USER_GUIDE.md` 里那条警告是真的 ✗：**这个项目没有任何登录** ✓ ——
一旦后端监听局域网，**同一个网络上的任何人**都能看你的记忆、
删你的消息、**下载你的完整备份** ✗。

## ⚠️ 它的能力边界（必须说清，别给人假安全感）

1. **密码走的是明文 HTTP** ✗ —— 在**共享网络**（校园网、咖啡厅）上，
   同一个网络里会抓包的人是**能拿到密码的** ✓。
   所以它防的是"随手扫到端口的人" ✓，**不是**"同一个 WiFi 上的抓包者" ✗。
   → **要真正安全：用手机热点或家里的网** ✓，别在校园网上开 ✓。
2. **没有防暴力破解** ✗（本机单人应用，加限流得不偿失 ✓）。
   所以密码别设成 `1234` ✓。

## 两条通道

| 谁 | 怎么带凭据 |
|---|---|
| 浏览器 | **HttpOnly Cookie**（登录一次就记住 ✓） |
| **脚本 / API 客户端** | `Authorization: Bearer <密码>` |

## Token 是**无状态**的

Cookie 里存的不是密码 ✗，是 `HMAC(密码, 固定串)` ✓ —— 于是：

- 重启后端**不用重新登录** ✓（没有内存里的会话表 ✓）
- **改了密码，所有旧 Cookie 立刻失效** ✓✓（这是对的 ✓）
"""

import hashlib
import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

COOKIE_NAME = "life_ai_pass"
_VERIFY_BASE = "life-ai-cookie-v1"

#: 不用密码就能访问的路径。**越少越好** ✓。
PUBLIC_PATHS = ("/login", "/api/v1/auth/login")


def password() -> str:
    """每次读环境变量 —— 改了密码不用重启（`settings_service` 会同步内存 ✓）。"""
    return (os.getenv("APP_PASSWORD") or "").strip()


def enabled() -> bool:
    return bool(password())


def token() -> str:
    """由密码派生的无状态 token ✓。"""
    return hmac.new(
        password().encode("utf-8"),
        _VERIFY_BASE.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def password_matches(value: str) -> bool:
    """校验**用户输入的那个密码**（登录用 ✓）。

    ⚠️ **和 `_matches()` 不是一回事**，别搞混 ✗：
      · `password_matches()` 比的是 **密码本身** ✓（登录请求里带的就是密码 ✓）
      · `_matches()` 比的是 **token** ✓（Cookie / Bearer 里带的是 HMAC ✓）

    2026-10-05 我把这两件事写成了一个函数 ✗ → 登录时拿"密码"去比 HMAC ✓
    → **永远不相等** ✗ → 登录接口从来没成功过 ✓（4 条测试当场抓住 ✓）。
    """
    return bool(value) and hmac.compare_digest(value, password())


def _matches(value: str) -> bool:
    """校验 **token**（Cookie / Bearer ✓）。"""
    return bool(value) and hmac.compare_digest(value, token())


def authorized(request: Request) -> bool:
    """Cookie（浏览器）或 Bearer（脚本 / API 客户端）任一条通过即可。"""
    if _matches(request.cookies.get(COOKIE_NAME, "")):
        return True

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        value = header[7:].strip()
        # ⚠️ Bearer 里**两种都认**：
        #   · token（HMAC ✓）—— 脚本自己算的场景，少见 ✓
        #   · **密码本身** —— 方便脚本 / API 客户端直接使用访问密码。
        #     不需要调用方手算 HMAC。
        #     → 文档写了"填密码"、代码却不认 ✓ → 测试当场抓住 ✓）。
        # 两者在 HTTP 上都是**明文** ✓，安全性上没有差别 ✓。
        return _matches(value) or password_matches(value)
    return False


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/login")


def reject(request: Request):
    """没凭据时的回应：页面 → 跳登录页 ✓；接口 → 401 ✓。"""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "需要访问密码。"}, status_code=401)
    return RedirectResponse("/login", status_code=302)


LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>life-ai · 登录</title>
<style>
 body { margin:0; min-height:100vh; display:grid; place-items:center;
        background:radial-gradient(circle at 15% 0%, #efebff 0, transparent 30rem), #f6f5f9;
        color:#24222b; font-family:Inter, ui-sans-serif, system-ui, sans-serif; }
 form { display:grid; gap:10px; width:min(340px, calc(100% - 40px));
        padding:22px; border:1px solid #e7e4ec; border-radius:18px; background:#fff; }
 h1 { margin:0 0 4px; font-size:17px; }
 p  { margin:0 0 6px; font-size:12px; color:#73707d; line-height:1.6; }
 input, button { font:inherit; padding:10px 12px; border-radius:11px; }
 input { border:1px solid #e7e4ec; }
 button { border:0; background:#6c5ce7; color:#fff; font-weight:700; }
 [hidden] { display:none !important; }
 #err { color:#b14b5c; font-size:12px; }
</style></head>
<body>
<form id="f">
  <h1>需要访问密码</h1>
  <p>这台电脑上的 life-ai 设了密码，防止同一个网络里的人看到你的记忆。</p>
  <input id="p" type="password" placeholder="访问密码" autocomplete="current-password" autofocus>
  <button type="submit">进入</button>
  <div id="err" hidden></div>
</form>
<script>
document.getElementById("f").addEventListener("submit", async (event) => {
  event.preventDefault();
  const err = document.getElementById("err");
  err.hidden = true;
  const response = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password: document.getElementById("p").value }),
  });
  if (response.ok) { location.replace("/"); return; }
  err.textContent = "密码不对。";
  err.hidden = false;
});
</script>
</body></html>
"""


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def post_login(data: LoginRequest):
    """登录：对了就发一个**无状态 Cookie** ✓。"""
    if not enabled():
        # 没设密码 = 不需要登录 ✓（正常前端走不到这里，但接口要自洽 ✓）
        return {"ok": True, "enabled": False}

    if not password_matches(data.password):
        # ⚠️ 用 401，不是 400 ✓ —— 这是"没通过认证"，不是"参数写错了" ✓
        raise HTTPException(status_code=401, detail="密码不对。")

    response = JSONResponse({"ok": True, "enabled": True})
    response.set_cookie(
        COOKIE_NAME,
        token(),
        httponly=True,  # ⚠️ 页面脚本读不到它 ✓（被 XSS 也偷不走 ✓）
        samesite="lax",  # ⚠️ 挡 CSRF ✓
        max_age=60 * 60 * 24 * 365,
        path="/",
    )
    return response


@router.post("/logout")
def post_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/status")
def get_status(request: Request):
    """前端可以据此显示"是否需要密码 / 我过了没有" ✓。"""
    if not enabled():
        return {"enabled": False, "authorized": True}
    return {"enabled": True, "authorized": authorized(request)}
