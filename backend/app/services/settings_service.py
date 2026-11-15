"""运行时模型与访问设置。

API Key 只写入 backend/.env，不进入数据库，也不会通过状态接口回显全文。
当前模型调用统一使用 OpenAI 兼容服务；Ollama 本地 Provider 已移除。
"""

import os
from pathlib import Path

from app.core import config
from app.core.exceptions import DomainValidationError, ExternalServiceError

DEAD_MODELS = ("deepseek-chat", "deepseek-reasoner")
RECOMMENDED_MODELS = ("deepseek-flash", "deepseek-v4-pro")


def env_path() -> Path:
    return Path(config.ENV_PATH)


def read_env_file() -> str:
    path = env_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def update_env_file(updates: dict[str, str]) -> None:
    """更新 .env，尽量保留原有注释、空行与顺序。"""
    remaining = dict(updates)
    out: list[str] = []
    for line in read_env_file().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{key}={value}" for key, value in remaining.items())
    env_path().write_text("\n".join(out) + "\n", encoding="utf-8")


def _apply_runtime(updates: dict[str, str]) -> None:
    """把新值同步到当前进程，使已创建的 provider 代理立即生效。"""
    for key, value in updates.items():
        os.environ[key] = value

    if "DEEPSEEK_API_KEY" in updates:
        config.DEEPSEEK_API_KEY = updates["DEEPSEEK_API_KEY"].strip()
    if "DEEPSEEK_MODEL" in updates:
        model = updates["DEEPSEEK_MODEL"].strip()
        config.DEEPSEEK_MODEL = model
        config.DEEPSEEK_CHAT_MODEL = model
        config.DEEPSEEK_VISION_MODEL = model


def _tier_status() -> dict:
    out: dict = {}
    for tier in config.TIERS:
        value = config.tier_config(tier)
        key = value["api_key"]
        out[tier] = {
            "provider": value["provider"],
            "base_url": value["base_url"],
            "model": value["model"],
            "api_key_configured": bool(key),
            "api_key_hint": f"…{key[-4:]}" if len(key) >= 4 else "",
            "customized": any(
                config.tier_env(tier, field)
                for field in ("PROVIDER", "BASE_URL", "API_KEY", "MODEL")
            ),
        }
    return out


def _configuration_warnings(factory) -> tuple[dict[str, str], list[str]]:
    providers: dict[str, str] = {}
    warnings: list[str] = []

    local_only = (os.getenv("LLM_LOCAL_ONLY") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if local_only:
        for task in factory.TASKS:
            providers[task] = "legacy_removed"
        warnings.append(
            "检测到旧配置 LLM_LOCAL_ONLY=true，但本地模型模式已移除。"
            "为避免隐私误判，AI 调用已暂停；请从 backend/.env 删除该项，再配置 OpenAI 兼容模型服务。"
        )
    else:
        for task in factory.TASKS:
            try:
                providers[task] = factory.provider_name_for(task)
            except DomainValidationError as exc:
                providers[task] = "legacy_removed"
                warnings.append(str(exc))

    legacy_keys = (
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "OLLAMA_NUM_CTX",
        "OLLAMA_TIMEOUT_SECONDS",
    )
    present = [key for key in legacy_keys if (os.getenv(key) or "").strip()]
    for task, env_key in factory._ENV_KEY_BY_TASK.items():
        if (os.getenv(env_key) or "").strip().lower() == "ollama":
            warnings.append(
                f"检测到 {env_key}=ollama（任务 {task}）；Ollama Provider 已移除，请从 backend/.env 删除该项。"
            )
    if present:
        warnings.append(
            "检测到已移除的 Ollama 配置：" + "、".join(present)
            + "。这些变量不再生效；请从 backend/.env 删除。"
        )
    return providers, list(dict.fromkeys(warnings))


def status() -> dict:
    """当前配置；不返回任何 API Key 全文。"""
    from app.llm import factory

    key = config.DEEPSEEK_API_KEY
    providers, warnings = _configuration_warnings(factory)
    return {
        "api_key_configured": bool(key),
        "api_key_hint": f"…{key[-4:]}" if len(key) >= 4 else "",
        "model": config.DEEPSEEK_CHAT_MODEL,
        "base_url": config.DEEPSEEK_BASE_URL,
        "recommended_models": list(RECOMMENDED_MODELS),
        "providers": providers,
        "tiers": _tier_status(),
        "configuration_warnings": warnings,
        "password_enabled": bool((os.getenv("APP_PASSWORD") or "").strip()),
        "env_path": str(env_path()),
    }


def save(
    api_key: str | None = None,
    model: str | None = None,
    tiers: dict | None = None,
    app_password: str | None = None,
) -> dict:
    updates: dict[str, str] = {}

    if api_key is not None and api_key.strip():
        updates["DEEPSEEK_API_KEY"] = api_key.strip()

    if model is not None and model.strip():
        chosen = model.strip()
        if chosen in DEAD_MODELS:
            raise DomainValidationError(
                f"`{chosen}` 已停用，填进去只会得到「模型不存在」。"
                f"请改用 {'、'.join(RECOMMENDED_MODELS)}。"
            )
        updates["DEEPSEEK_MODEL"] = chosen

    if tiers:
        for tier, fields in tiers.items():
            if tier not in config.TIERS:
                raise DomainValidationError(
                    f"不认识的档：{tier}（可选：{'、'.join(config.TIERS)}）"
                )
            if not isinstance(fields, dict):
                raise DomainValidationError(f"模型档 {tier} 的配置格式不正确。")
            for field in ("provider", "base_url", "api_key", "model"):
                if field not in fields:
                    continue
                value = str(fields[field] or "").strip()
                if field == "provider" and value and value.lower() not in ("deepseek", "openai"):
                    if value.lower() == "ollama":
                        raise DomainValidationError(
                            "Ollama 已移除。请改用 deepseek / openai（OpenAI 兼容服务）。"
                        )
                    raise DomainValidationError(
                        f"不认识的 provider：{value}（当前只支持 deepseek / openai）。"
                    )
                updates[f"LLM_TIER_{tier.upper()}_{field.upper()}"] = value

    if app_password is not None:
        clean_password = app_password.strip()
        if clean_password and len(clean_password) < 6:
            raise DomainValidationError(
                "访问密码至少要 6 位 —— 它保护的是你全部的记忆。"
            )
        updates["APP_PASSWORD"] = clean_password

    if not updates:
        raise DomainValidationError("没有要保存的内容。")

    update_env_file(updates)
    _apply_runtime(updates)

    from app.llm import factory
    factory.reset_cache()
    return status()


def test_connection() -> dict:
    """向当前聊天档发送最小请求，验证 API Key、地址与模型名。"""
    from app.llm import factory

    provider = factory.get_provider("chat")
    if not provider.available:
        return {"ok": False, "detail": "还没有填 API Key。"}
    try:
        reply = provider.chat(
            [{"role": "user", "content": "只回复两个字：可用"}],
            temperature=0,
        )
    except ExternalServiceError as exc:
        return {"ok": False, "detail": str(exc)[:400]}
    return {
        "ok": True,
        "reply": reply[:80],
        "model": provider.model,
        "usage": provider.last_usage,
    }
