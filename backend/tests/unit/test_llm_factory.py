"""Provider 工厂：所有任务统一走 OpenAI 兼容服务。"""

import json

import httpx
import pytest

from app.core import config
from app.core.exceptions import DomainValidationError
from app.llm import factory
from app.llm.deepseek import OpenAICompatProvider


@pytest.fixture(autouse=True)
def clean_cache():
    factory.reset_cache()
    yield
    factory.reset_cache()


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


def _capture_post(monkeypatch, content: str = "{}") -> list[dict]:
    seen: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("app.llm.deepseek.httpx.post", fake_post)
    return seen


def test_every_task_defaults_to_openai_compatible_provider():
    for task in factory.TASKS:
        assert factory.provider_name_for(task) == "deepseek"
        assert isinstance(factory.get_provider(task), OpenAICompatProvider)


def test_extract_follows_the_analysis_tier(monkeypatch):
    monkeypatch.setenv("LLM_TIER_ANALYSIS_MODEL", "analysis-model")
    factory.reset_cache()
    provider = factory.get_provider("extract")
    assert provider.model == "analysis-model"


def test_task_can_select_openai_alias(monkeypatch):
    monkeypatch.setenv("LLM_TIER_ROLEPLAY_PROVIDER", "openai")
    factory.reset_cache()
    assert factory.provider_name_for("chat") == "openai"
    assert isinstance(factory.get_provider("chat"), OpenAICompatProvider)


def test_unknown_task_raises():
    with pytest.raises(DomainValidationError):
        factory.get_provider("summarize")


def test_removed_ollama_configuration_fails_explicitly(monkeypatch):
    monkeypatch.setenv("LLM_TIER_ANALYSIS_PROVIDER", "ollama")
    factory.reset_cache()
    with pytest.raises(DomainValidationError, match="已移除的 Ollama"):
        factory.get_provider("extract")


def test_unknown_provider_name_raises(monkeypatch):
    monkeypatch.setenv("LLM_TIER_ROLEPLAY_PROVIDER", "gpt")
    factory.reset_cache()
    with pytest.raises(DomainValidationError, match="不认识的 provider"):
        factory.get_provider("chat")


def test_the_same_provider_instance_is_reused():
    assert factory.get_provider("chat") is factory.get_provider("chat")


def test_two_tiers_do_not_share_a_provider_instance(monkeypatch):
    assert factory.get_provider("chat") is not factory.get_provider("vision")


def test_vision_uses_its_own_model_slot(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_VISION_MODEL", "vision-model-x")
    factory.reset_cache()
    assert factory.get_provider("vision").model == "vision-model-x"


def test_json_mode_sets_response_format(monkeypatch):
    seen = _capture_post(monkeypatch, content='{"ok": true}')
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    OpenAICompatProvider().chat(
        [{"role": "user", "content": "抽取"}], json_mode=True
    )
    assert seen[0]["json"]["response_format"] == {"type": "json_object"}


def test_json_mode_is_absent_by_default(monkeypatch):
    seen = _capture_post(monkeypatch, content="好的")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    OpenAICompatProvider().chat([{"role": "user", "content": "在吗"}])
    assert "response_format" not in seen[0]["json"]


def test_vision_blocks_pass_through(monkeypatch):
    seen = _capture_post(monkeypatch, content="识别结果")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    blocks = [
        {"type": "text", "text": "转成文字，只输出表格内容"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA", "detail": "low"}},
    ]
    result = OpenAICompatProvider().chat([{"role": "user", "content": blocks}])
    assert seen[0]["json"]["messages"][0]["content"] == blocks
    assert result == "识别结果"


def test_legacy_local_only_flag_blocks_ai_requests(monkeypatch):
    monkeypatch.setenv("LLM_LOCAL_ONLY", "true")
    factory.reset_cache()
    with pytest.raises(DomainValidationError, match="AI 调用已暂停"):
        factory.get_provider("chat")
