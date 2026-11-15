"""按任务与能力档位选择 OpenAI 兼容模型服务。"""

import os

from app.core import config
from app.core.exceptions import DomainValidationError
from app.llm.base import LLMProvider
from app.llm.deepseek import OpenAICompatProvider

TASKS = ("chat", "extract", "impression", "review", "vision", "deadline_breakdown")

DEFAULT_PROVIDER_BY_TASK = {
    "chat": "deepseek",
    "extract": "deepseek",
    "impression": "deepseek",
    "review": "deepseek",
    "vision": "deepseek",
    "deadline_breakdown": "deepseek",
}

_ENV_KEY_BY_TASK = {
    "chat": "LLM_CHAT_PROVIDER",
    "extract": "LLM_EXTRACT_PROVIDER",
    "impression": "LLM_IMPRESSION_PROVIDER",
    "review": "LLM_REVIEW_PROVIDER",
    "vision": "LLM_VISION_PROVIDER",
    "deadline_breakdown": "LLM_DEADLINE_BREAKDOWN_PROVIDER",
}

_instances: dict[tuple[str, str], LLMProvider] = {}


def reset_cache() -> None:
    """清掉实例缓存。设置保存后调用，测试也会调用。"""
    _instances.clear()


def provider_name_for(task: str) -> str:
    """返回任务当前配置的 provider 名称。

    `deepseek` 与 `openai` 都由同一个 OpenAICompatProvider 实现。
    已移除的 `ollama` 不会被静默改路由，以免旧配置造成未告知的数据外发。
    """
    if task not in TASKS:
        raise DomainValidationError(
            f"不认识的任务标识：{task}（可选：{'、'.join(TASKS)}）"
        )

    if (os.getenv("LLM_LOCAL_ONLY") or "").strip().lower() in {"1", "true", "yes", "on"}:
        raise DomainValidationError(
            "检测到旧配置 LLM_LOCAL_ONLY=true，但本地模型模式已移除。"
            "为避免把你以为留在本机的内容发往远程服务，AI 调用已暂停；"
            "请从 backend/.env 删除该项，再配置 OpenAI 兼容模型服务。"
        )

    tier = config.TASK_TIER.get(task)
    from_tier = config.tier_env(tier, "PROVIDER") if tier else ""
    configured = from_tier or getattr(config, _ENV_KEY_BY_TASK[task], "") or ""
    name = str(configured).strip().lower() or DEFAULT_PROVIDER_BY_TASK[task]

    if name == "ollama":
        raise DomainValidationError(
            f"任务 {task} 仍配置为已移除的 Ollama。请在设置页保存对应模型档，"
            "改用 OpenAI 兼容服务；也请从 backend/.env 删除旧的 Ollama 配置。"
        )
    if name not in ("deepseek", "openai"):
        raise DomainValidationError(
            f"不认识的 provider：{name}（任务 {task}）。"
            "当前只支持 deepseek / openai（OpenAI 兼容服务）。"
        )
    return name


def get_provider(task: str) -> LLMProvider:
    """取得某个任务对应的真实 provider 实例。"""
    name = provider_name_for(task)
    tier = config.TASK_TIER.get(task)
    model = None
    if task == "vision" and not config.tier_env("vision", "MODEL"):
        model = config.DEEPSEEK_VISION_MODEL

    key = (name, f"{tier}:{model or 'default'}")
    if key not in _instances:
        _instances[key] = OpenAICompatProvider(model=model, tier=tier)
    return _instances[key]


class _CurrentProvider:
    """永远指向当前配置的 provider 代理。"""

    __slots__ = ("_task",)

    def __init__(self, task: str) -> None:
        self._task = task

    @property
    def _target(self) -> LLMProvider:
        return get_provider(self._task)

    def __getattr__(self, name: str):
        return getattr(self._target, name)


def current_provider(task: str) -> LLMProvider:
    """给长期持有 provider 的模块使用；设置变更后无需重启。"""
    if task not in TASKS:
        raise DomainValidationError(
            f"不认识的任务标识：{task}（可选：{'、'.join(TASKS)}）"
        )
    return _CurrentProvider(task)  # type: ignore[return-value]
