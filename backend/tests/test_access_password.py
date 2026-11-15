"""访问密码（局域网访问用）。

**默认不启用** ✓ —— `APP_PASSWORD` 为空时，一切和以前一样 ✓。
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth


@pytest.fixture
def guarded(monkeypatch):
    """设上密码，并**重建 app**（`TestClient` 用的是同一份 app ✓）。"""
    monkeypatch.setenv("APP_PASSWORD", "correct-horse")
    yield "correct-horse"
    monkeypatch.delenv("APP_PASSWORD", raising=False)


def test_it_is_disabled_by_default(client):
    """★ 没设密码时**一切照旧** ✓ —— 本机用法不受任何影响 ✓。"""
    assert auth.enabled() is False
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/export/json").status_code == 200


def test_a_protected_route_needs_the_password(client, guarded):
    assert client.get("/api/v1/health").status_code == 401


def test_the_login_page_is_reachable(client, guarded):
    """⚠️ 登录页自己当然不能被挡住 ✓（否则死锁 ✓）。"""
    page = client.get("/login")

    assert page.status_code == 200
    assert "访问密码" in page.text


def test_logging_in_sets_a_cookie_and_unlocks(client, guarded):
    assert client.post(
        "/api/v1/auth/login", json={"password": "correct-horse"}
    ).status_code == 200

    assert client.get("/api/v1/health").status_code == 200


def test_a_wrong_password_is_401_not_400(client, guarded):
    """⚠️ 401 = "没通过认证" ✓；400 = "参数写错了" ✗。别混 ✓。"""
    response = client.post("/api/v1/auth/login", json={"password": "nope"})

    assert response.status_code == 401


def test_the_bearer_header_works_too(client, guarded):
    """★ 脚本 / API 客户端可用 Bearer 密码访问。"""
    response = client.get(
        "/api/v1/health",
        headers={"Authorization": "Bearer correct-horse"},
    )

    assert response.status_code == 200


def test_the_cookie_never_contains_the_password(client, guarded):
    """★ Cookie 里存的是 HMAC，**不是密码** ✓。"""
    client.post("/api/v1/auth/login", json={"password": "correct-horse"})

    cookie = client.cookies.get(auth.COOKIE_NAME)

    assert cookie == auth.token()
    assert "correct-horse" not in cookie


def test_changing_the_password_invalidates_old_cookies(client, guarded, monkeypatch):
    """★ 无状态 token 的直接好处 ✓：改密码 → 旧 Cookie 立刻失效 ✓。"""
    client.post("/api/v1/auth/login", json={"password": "correct-horse"})
    assert client.get("/api/v1/health").status_code == 200

    monkeypatch.setenv("APP_PASSWORD", "a-new-password")

    assert client.get("/api/v1/health").status_code == 401


def test_logging_out_locks_again(client, guarded):
    client.post("/api/v1/auth/login", json={"password": "correct-horse"})
    client.post("/api/v1/auth/logout")

    assert client.get("/api/v1/health").status_code == 401


def test_the_backup_endpoint_is_protected_too(client, guarded):
    """★★ **这条最重要。**

    没有密码的时候，同一个 WiFi 上的人可以直接把**全部记忆**下载走 ✗
    —— `/api/v1/export/backup.zip` 是那个洞 ✓。
    """
    assert client.get("/api/v1/export/backup.zip").status_code == 401


# ---------- 从界面上设（不用重启、不用重新登录） ----------


@pytest.fixture
def temp_env(monkeypatch, tmp_path):
    """⚠️ 必须把 `.env` 指向临时文件 —— 否则测试会**写进用户真实的 `.env`** ✗。"""
    from app.core import config

    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")


def test_it_can_be_set_from_the_settings_api(client, temp_env):
    """★ 设密码 → **立刻生效** ✓（不用重启 ✓）。"""
    response = client.post("/api/v1/settings", json={"app_password": "hunter2x"})

    assert response.status_code == 200
    assert response.json()["password_enabled"] is True


def test_the_current_browser_is_logged_in_at_the_same_time(client, temp_env):
    """★★ **这条是为了不让用户以为"填密码把应用弄坏了"** ✓。

    密码一存上，如果后端不顺手种 Cookie，这个页面**下一次请求就被自己拦成 401** ✗
    —— 用户看到的就是"页面突然打不开了" ✓。
    """
    client.post("/api/v1/settings", json={"app_password": "hunter2x"})

    assert client.get("/api/v1/health").status_code == 200


def test_a_stranger_without_the_cookie_is_still_blocked(client, temp_env):
    """★ 顺手登录**只对当前这台浏览器** ✓ —— 别人照样进不来 ✓。"""
    from app.main import app

    client.post("/api/v1/settings", json={"app_password": "hunter2x"})

    with TestClient(app) as stranger:
        assert stranger.get("/api/v1/health").status_code == 401


def test_a_short_password_is_rejected(client, temp_env):
    """它保护的是**全部记忆** ✓ —— 6 位是底线 ✓。"""
    assert (
        client.post("/api/v1/settings", json={"app_password": "123"}).status_code
        == 400
    )


def test_it_can_be_cleared_from_the_api(client, temp_env):
    client.post("/api/v1/settings", json={"app_password": "hunter2x"})

    response = client.post("/api/v1/settings", json={"app_password": ""})

    assert response.json()["password_enabled"] is False
    assert client.get("/api/v1/health").status_code == 200


def test_the_status_never_echoes_the_password(client, temp_env):
    client.post("/api/v1/settings", json={"app_password": "hunter2x"})

    assert "hunter2x" not in str(client.get("/api/v1/settings").json())
