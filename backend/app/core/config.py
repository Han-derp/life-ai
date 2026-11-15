import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_DIR / ".env"
load_dotenv(ENV_PATH)


def _resolve_db_path(raw: str | None) -> Path:
    if not raw:
        return BACKEND_DIR / "storage" / "app.db"
    path = Path(raw)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
APP_DB_PATH = _resolve_db_path(os.getenv("APP_DB_PATH"))

# S3：默认启用人格，对应 backend/personas/<name>/persona.md。
# 运行时可被 app_settings 里的 'persona' 覆盖（应用内切换不需要重启、不需要改 .env）。
# 2026-10-05：默认人格换成「行动搭档」（companion）—— 不是人物 ✓（见 AI_COMPANION_REDESIGN.md）。
PERSONA = os.getenv("PERSONA", "companion")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
# ⚠️ `deepseek-chat` / `deepseek-reasoner` 已停用（docs/MODEL_USAGE_SPEC.md §0）。
# 默认值也必须跟着改：默认值里留着死名字，会在 .env 缺这一行时静默 400。
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "1.0"))
DEEPSEEK_TIMEOUT_SECONDS = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))

# S1 起：system prompt 各块（persona + rules + today）共用一个总预算，
# 超预算时按优先级整块丢弃，不再按字符硬切。
# 旧的 CONTEXT_PERSONA_BUDGET / CONTEXT_TODAY_BUDGET 分段预算已废弃（仅被字符硬切使用）。
# ⚠️ **2026-10-05：2000 → 3000** ✗✓
#
# 为什么：人格文件涨到 **2053 token** ✓（用户扩写过 ✓），
# 而**人格与规则是"最后丢、绝不丢"的** ✓ → 2000 的预算**光人格就爆了** ✗✓
# → `assemble()` 里**记忆和今天的数据会全被丢掉** ✗
#（实测被 `test_now_is_not_sent_to_model` 抓到 ✓ —— 当地日期没了 ✓，
#  也就是说她**不知道今天是几号** ✓）。
#
# 实测占用 ≈ **2873** ✓（persona 2053 + rules 495 + memory 220 + today 105 ✓）→ 3000 装得下 ✓。
#
# ⚠️ 它**只管 `assemble()`** ✓ —— 聊天真正走的是 `plan_context()` + `CONTEXT_TOTAL_BUDGET`（4000 ✓）✓。
CONTEXT_SYSTEM_BUDGET = int(os.getenv("CONTEXT_SYSTEM_BUDGET", "3000"))
CONTEXT_RECENT_CHAT_BUDGET = int(os.getenv("CONTEXT_RECENT_CHAT_BUDGET", "2000"))
# S2 起：system 块 + 最近聊天共用一个总预算，面板底部显示"合计 / 预算"。
CONTEXT_TOTAL_BUDGET = int(os.getenv("CONTEXT_TOTAL_BUDGET", "4000"))

# S8 主动联系。默认开着，但**第一次 tick 在一个完整间隔之后**
# （见 scheduler.runner），所以短命进程——尤其是测试——不会被波及。
PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}
PROACTIVE_TICK_SECONDS = int(os.getenv("PROACTIVE_TICK_SECONDS", "300"))

# ---------- 模型分工（docs/MODEL_USAGE_SPEC.md 是唯一依据，不要自行改分工） ----------

# 云端模型。deepseek-flash 支持视觉，且缓存命中后近乎免费；
# deepseek-chat / deepseek-reasoner 已停用（§0）。
# 下面两个具体槽位默认都跟随 DEEPSEEK_MODEL，所以 .env 里仍只需一行（§1.2）。
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", DEEPSEEK_MODEL)
DEEPSEEK_VISION_MODEL = os.getenv("DEEPSEEK_VISION_MODEL", DEEPSEEK_MODEL)

# 按任务分派的开关：改这里 + 重启即生效，业务代码一行都不用动。
# 当前只支持 OpenAI 兼容服务（deepseek / openai 是同一实现的配置别名）。
LLM_CHAT_PROVIDER = os.getenv("LLM_CHAT_PROVIDER", "deepseek")
LLM_EXTRACT_PROVIDER = os.getenv("LLM_EXTRACT_PROVIDER", "deepseek")
LLM_IMPRESSION_PROVIDER = os.getenv("LLM_IMPRESSION_PROVIDER", "deepseek")
LLM_REVIEW_PROVIDER = os.getenv("LLM_REVIEW_PROVIDER", "deepseek")
LLM_VISION_PROVIDER = os.getenv("LLM_VISION_PROVIDER", "deepseek")
LLM_DEADLINE_BREAKDOWN_PROVIDER = os.getenv(
    "LLM_DEADLINE_BREAKDOWN_PROVIDER", "deepseek"
)

# ---------- 模型频道（三档） ----------
#
# 用户要求：把任务按"需要什么能力"分档，然后在界面上**自己填**想用的模型。
#
#   roleplay（要人格一致性）→ chat / 主动联系 / 每日回顾
#   analysis（要听话、严格 JSON、不编造）→ 记忆抽取 / 印象归纳
#   vision（要多模态）        → 图片识别
#
# ⚠️ **每一档都是"可选覆盖"**：留空就沿用 `DEEPSEEK_*` 那套。
# 所以现有配置和 500+ 条测试的行为**一个字节都不变** ✓ ——
# 这是刻意的：新增能力不该改默认行为。
TASK_TIER = {
    "chat": "roleplay",
    "proactive": "roleplay",
    "review": "roleplay",
    # DDL 拆解是同步交互，独立任务名可避免后续调整聊天模型时意外改变它的路由。
    "deadline_breakdown": "roleplay",
    "extract": "analysis",
    "impression": "analysis",
    "vision": "vision",
}

TIERS = ("roleplay", "analysis", "vision")

#: 各档的默认 provider。当前所有档均使用 OpenAI 兼容服务。
#: `deepseek` 是默认配置名；也可在档位中填写自建或其他 OpenAI 兼容服务。
#: AI 请求会发送到该档配置的 base_url，若使用第三方云服务，相关上下文会离开本机。
DEFAULT_TIER_PROVIDER = {
    "roleplay": "deepseek",
    "analysis": "deepseek",
    "vision": "deepseek",
}


def tier_env(tier: str, field: str) -> str:
    """某一档的原始配置值（空字符串 = 没设，走回落）。"""
    return (os.getenv(f"LLM_TIER_{tier.upper()}_{field}") or "").strip()


def tier_config(tier: str) -> dict:
    """某一档**解析后**的接口配置。**每次读** —— 界面保存后立刻生效 ✓。

    留空的字段回落到 `DEEPSEEK_*`，所以老配置不用改一行就能继续跑 ✓。
    """
    provider = tier_env(tier, "PROVIDER") or DEFAULT_TIER_PROVIDER.get(
        tier, "deepseek"
    )
    return {
        "provider": provider.lower(),
        "base_url": tier_env(tier, "BASE_URL") or DEEPSEEK_BASE_URL,
        "api_key": tier_env(tier, "API_KEY") or DEEPSEEK_API_KEY,
        "model": tier_env(tier, "MODEL") or DEEPSEEK_CHAT_MODEL,
    }
