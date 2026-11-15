"""三档模型配置（陪聊 / 硬核 / 看图）。

用户要求：把任务按"需要什么能力"分档，自己在界面上填模型。

**最关键的性质是"默认行为不变"** —— 档里留空就沿用 `DEEPSEEK_*`，
所以老 `.env` 和 500+ 条已有测试一个字节都不用改 ✓。
"""

import pytest

from app.core import config
from app.core.exceptions import DomainValidationError
from app.llm import factory
from app.services import settings_service


@pytest.fixture
def clean_tiers(monkeypatch):
    """把三档清空，并清掉 provider 实例缓存。"""
    for tier in config.TIERS:
        for field in ("PROVIDER", "BASE_URL", "API_KEY", "MODEL"):
            monkeypatch.delenv(f"LLM_TIER_{tier.upper()}_{field}", raising=False)
    factory.reset_cache()
    yield
    factory.reset_cache()


# ---------- 任务 → 档 ----------


def test_tasks_map_to_the_right_tier():
    assert config.TASK_TIER["chat"] == "roleplay"
    assert config.TASK_TIER["extract"] == "analysis"
    assert config.TASK_TIER["impression"] == "analysis"
    assert config.TASK_TIER["vision"] == "vision"


def test_deadline_breakdown_uses_the_roleplay_tier():
    """★ R15：DDL 拆解是**同步**的（用户点一下就在等结果 ✓）。

    给它独立任务名的意义就是这条不变量：
    **以后有人改"聊天用什么模型"时，不会意外改变 DDL 拆解的模型配置**。
    """
    assert "deadline_breakdown" in factory.TASKS
    assert config.TASK_TIER["deadline_breakdown"] == "roleplay"
    assert config.TASK_TIER["deadline_breakdown"] != "analysis"
    assert factory.provider_name_for("deadline_breakdown") == "deepseek"


# ---------- 默认行为不变（最重要） ----------


def test_an_empty_tier_falls_back_to_deepseek(clean_tiers):
    value = config.tier_config("roleplay")

    assert value["base_url"] == config.DEEPSEEK_BASE_URL
    assert value["api_key"] == config.DEEPSEEK_API_KEY
    assert value["model"] == config.DEEPSEEK_CHAT_MODEL


def test_without_tier_config_the_routing_is_unchanged(clean_tiers, monkeypatch):
    """没填档时，逐任务 `.env` 说了算 —— 和今天完全一样。"""
    monkeypatch.setattr(config, "LLM_EXTRACT_PROVIDER", "openai")

    assert factory.provider_name_for("extract") == "openai"


# ---------- 填了档就以档为准 ----------


def test_a_tier_override_reaches_its_tasks(clean_tiers, monkeypatch):
    monkeypatch.setenv("LLM_TIER_ROLEPLAY_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_TIER_ROLEPLAY_MODEL", "some-roleplay-model")
    factory.reset_cache()

    provider = factory.get_provider("chat")

    assert provider.base_url == "https://example.com/v1"
    assert provider.model == "some-roleplay-model"


def test_the_tier_provider_wins_over_the_task_env(clean_tiers, monkeypatch):
    """★ 优先级：档 > 逐任务 `.env`。

    硬核档配置应优先于任务级环境变量。
    """
    monkeypatch.setattr(config, "LLM_EXTRACT_PROVIDER", "openai")
    monkeypatch.setenv("LLM_TIER_ANALYSIS_PROVIDER", "deepseek")

    assert factory.provider_name_for("extract") == "deepseek"


def test_two_tiers_do_not_share_a_provider_instance(clean_tiers, monkeypatch):
    """档必须进实例缓存的 key。

    不带 tier 的话，两档同名模型会共用实例，而实例上的 `last_usage` 会串 ✗
    （S2 面板的"缓存命中"就是从它读的）。
    """
    factory.reset_cache()

    roleplay = factory.get_provider("chat")
    vision = factory.get_provider("vision")

    assert roleplay is not vision


# ---------- 保存到 .env ----------


def test_saving_a_tier_writes_the_env_keys(clean_tiers, monkeypatch):
    written: dict = {}
    monkeypatch.setattr(
        settings_service, "update_env_file", lambda updates: written.update(updates)
    )
    monkeypatch.setattr(settings_service, "_apply_runtime", lambda updates: None)

    settings_service.save(
        tiers={"roleplay": {"base_url": "https://x/v1", "model": "m1"}}
    )

    assert written["LLM_TIER_ROLEPLAY_BASE_URL"] == "https://x/v1"
    assert written["LLM_TIER_ROLEPLAY_MODEL"] == "m1"


def test_an_unknown_tier_is_rejected(clean_tiers):
    with pytest.raises(DomainValidationError, match="不认识的档"):
        settings_service.save(tiers={"whatever": {"model": "m"}})


def test_an_unknown_provider_is_rejected(clean_tiers):
    with pytest.raises(DomainValidationError, match="不认识的 provider"):
        settings_service.save(tiers={"roleplay": {"provider": "gpt"}})


def test_status_never_echoes_the_key(client, clean_tiers, monkeypatch):
    """三档的状态里**绝不能有 Key 全文** —— 和 DEEPSEEK_API_KEY 同一条规矩。"""
    monkeypatch.setenv("LLM_TIER_ROLEPLAY_API_KEY", "sk-secret-1234")

    body = client.get("/api/v1/settings").json()

    assert "sk-secret-1234" not in str(body)
    assert body["tiers"]["roleplay"]["api_key_hint"] == "…1234"
    assert body["tiers"]["roleplay"]["customized"] is True


def test_status_surfaces_removed_ollama_settings(client, clean_tiers, monkeypatch):
    monkeypatch.setenv("LLM_TIER_ANALYSIS_PROVIDER", "ollama")
    factory.reset_cache()

    body = client.get("/api/v1/settings").json()

    assert body["providers"]["extract"] == "legacy_removed"
    assert any("已移除的 Ollama" in item for item in body["configuration_warnings"])


def test_status_pauses_all_ai_routes_for_legacy_local_only(client, clean_tiers, monkeypatch):
    monkeypatch.setenv("LLM_LOCAL_ONLY", "true")
    factory.reset_cache()

    body = client.get("/api/v1/settings").json()

    assert set(body["providers"].values()) == {"legacy_removed"}
    assert any("AI 调用已暂停" in item for item in body["configuration_warnings"])
