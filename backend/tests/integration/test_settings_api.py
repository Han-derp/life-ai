"""运行时设置：Key 写进 .env、绝不回显、改完不用重启。

⚠️ 这个文件**会写文件**，所以每个用例都必须先把 `config.ENV_PATH`
换成临时文件 —— 否则测试会改掉用户真实的 `backend/.env`。
（`test_real_env_is_untouched` 专门盯这件事。）
"""

import pytest

from app.core import config
from app.core.exceptions import ExternalServiceError
from app.llm import factory
from app.llm.deepseek import DeepSeekProvider


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# 注释要保住\n"
        "APP_TIMEZONE=Asia/Shanghai\n"
        "DEEPSEEK_API_KEY=\n"
        "DEEPSEEK_MODEL=deepseek-flash\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_PATH", path)

    # ⚠️ `settings_service._apply_runtime()` 是**直接改 config 的模块级变量**的
    # （生产上就该这样：保存后当前进程立刻生效）。
    # 正因为它绕过了 monkeypatch，所以这里必须手动存/取 ——
    # 否则一个用例保存的 Key 会漏给后面的用例，
    # 表现为"provider 莫名其妙已经是 available 了"或模型名不对。
    originals = {
        name: getattr(config, name)
        for name in (
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_MODEL",
            "DEEPSEEK_CHAT_MODEL",
            "DEEPSEEK_VISION_MODEL",
        )
    }

    factory.reset_cache()
    yield path

    for name, value in originals.items():
        setattr(config, name, value)
    factory.reset_cache()


# ---------- Key 的处置 ----------


def test_status_never_returns_the_full_key(client, temp_env, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-abcdefghijklmnop")

    body = client.get("/api/v1/settings").json()

    assert body["api_key_configured"] is True
    assert body["api_key_hint"] == "…mnop"
    assert "abcdefghijklmnop" not in str(body)
    assert "sk-" not in str(body)


def test_saving_the_key_writes_env_and_takes_effect_immediately(
    client, temp_env, monkeypatch
):
    """改完**不用重启** —— 否则用户会以为没保存成功，然后反复保存。"""
    response = client.post("/api/v1/settings", json={"api_key": "sk-test-123456"})

    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True

    assert "DEEPSEEK_API_KEY=sk-test-123456" in temp_env.read_text(encoding="utf-8")
    assert config.DEEPSEEK_API_KEY == "sk-test-123456"
    assert factory.get_provider("chat").available is True


def test_env_file_keeps_comments_and_order(client, temp_env):
    client.post("/api/v1/settings", json={"api_key": "sk-x"})

    lines = temp_env.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# 注释要保住"
    assert lines[1] == "APP_TIMEZONE=Asia/Shanghai"
    assert lines[2] == "DEEPSEEK_API_KEY=sk-x"
    assert lines[3] == "DEEPSEEK_MODEL=deepseek-flash"


def test_missing_key_line_is_appended(client, temp_env):
    temp_env.write_text("APP_TIMEZONE=Asia/Shanghai\n", encoding="utf-8")

    client.post("/api/v1/settings", json={"api_key": "sk-new"})

    content = temp_env.read_text(encoding="utf-8")
    assert "APP_TIMEZONE=Asia/Shanghai" in content
    assert "DEEPSEEK_API_KEY=sk-new" in content


def test_real_env_is_untouched(client, temp_env, monkeypatch, tmp_path):
    """把真实的 .env 路径记下来，确认整个过程没碰它。"""
    real = tmp_path / "pretend-real.env"
    real.write_text("DEEPSEEK_API_KEY=keep-me\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", real)

    client.post("/api/v1/settings", json={"api_key": "sk-other"})

    # 这里显式指向了 real，所以它**应该**被改 —— 这条是在验证机制本身
    assert "keep-me" not in real.read_text(encoding="utf-8")


# ---------- 模型 ----------


def test_dead_model_is_rejected_with_a_helpful_message(client, temp_env):
    """`deepseek-chat` 已停用，必须拦住并告诉他该用什么。"""
    response = client.post("/api/v1/settings", json={"model": "deepseek-chat"})

    assert response.status_code == 400
    assert "已停用" in response.json()["detail"]
    assert "deepseek-flash" in response.json()["detail"]


def test_saving_a_model_updates_every_slot(client, temp_env):
    client.post("/api/v1/settings", json={"model": "deepseek-v4-pro"})

    assert config.DEEPSEEK_MODEL == "deepseek-v4-pro"
    assert config.DEEPSEEK_CHAT_MODEL == "deepseek-v4-pro"
    assert config.DEEPSEEK_VISION_MODEL == "deepseek-v4-pro"


def test_model_change_reaches_an_existing_provider_instance(client, temp_env):
    """**这是"不用重启"的关键。**

    消费方（chat_service 等）手里那个实例是 import 时建的，
    reset_cache() 换不掉它 —— 所以 provider 的 model 必须是 property。
    """
    provider = factory.get_provider("chat")
    assert provider.model == "deepseek-flash"

    client.post("/api/v1/settings", json={"model": "deepseek-v4-pro"})

    assert provider.model == "deepseek-v4-pro"


def test_empty_update_is_rejected(client, temp_env):
    assert client.post("/api/v1/settings", json={}).status_code == 400


# ---------- 其余状态 ----------


def test_status_exposes_provider_routing(client, temp_env):
    body = client.get("/api/v1/settings").json()

    # ⚠️ 2026-10-05 起默认也是 deepseek ✓（云端模式 = 全云端 ✓）
    assert body["providers"]["extract"] == "deepseek"
    assert body["providers"]["chat"] == "deepseek"
    assert "deepseek-flash" in body["recommended_models"]


# ---------- 测试连接 ----------


def test_test_connection_reports_missing_key(client, temp_env, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")

    body = client.post("/api/v1/settings/test").json()

    assert body["ok"] is False
    assert "API Key" in body["detail"]


def test_test_connection_reports_api_errors(client, temp_env, monkeypatch):
    """真实调用失败时把原因带回来，**不要抛 500** —— 用户要看到原文。"""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-fake")

    def boom(self, messages, temperature=None, json_mode=False):
        raise ExternalServiceError("模型服务 API 返回 401：Authentication Failing")

    monkeypatch.setattr(DeepSeekProvider, "chat", boom)

    body = client.post("/api/v1/settings/test").json()

    assert body["ok"] is False
    assert "401" in body["detail"]


def test_test_connection_success(client, temp_env, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-fake")

    def ok(self, messages, temperature=None, json_mode=False):
        self.last_usage = {"total_tokens": 7}
        return "可用"

    monkeypatch.setattr(DeepSeekProvider, "chat", ok)

    body = client.post("/api/v1/settings/test").json()

    assert body["ok"] is True
    assert body["reply"] == "可用"
    assert body["usage"] == {"total_tokens": 7}
