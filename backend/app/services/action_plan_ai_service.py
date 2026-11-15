"""让 AI 出一份**行动计划草稿** ✓。

## ⚠️ 这个功能**不注入人格** ✗（用户 2026-10-05 明确要求 ✓）

它是一个**工具性**的功能 ✓ —— 要的是"能不能给出能执行的步骤" ✓，
不是"像不像她" ✗。所以这里**不**走 `plan_context()` ✓，
也不带人格 / 规则 / 记忆 ✓ —— 只给"今天的真实状态" ✓。

## 三条必须守的规矩

1. **prompt 放在 `app/prompts/action_plan.md`** ✓ —— 项目的规矩是提示词不硬编码 ✗
2. **失败必须让用户看见** ✗ —— 不吞异常 ✓；兜底模板可以给 ✓，
   但返回的 `source` 必须是 `"template"` ✓，界面据此说明 ✓
3. **上下文要有上限** ✓ —— 今天的真实状态可能很长 ✓，
   整段塞进 prompt 会顶满预算 ✗（`_CONTEXT_CHARS` ✓）
"""

import json
import logging

from app.core.exceptions import ExternalServiceError
from app.core.prompt_loader import load_prompt
from app.llm import factory
from app.schemas.action_plan import (
    ActionPlanCreate,
    ActionPlanDraftResponse,
    ActionStep,
)
from app.services.context_service import get_today_context

logger = logging.getLogger(__name__)

#: 喂给模型"今天状态"的字符上限 ✓。
#: 约 2000 字符 ≈ 700 token ✓ —— 够它判断"今天还剩多少时间" ✓，又不会顶满预算 ✓。
_CONTEXT_CHARS = 2000


def _template(message: str) -> ActionPlanDraftResponse:
    """AI 没成功时的**保底模板** ✓。

    ⚠️ `source="template"` **必须**带上 ✓ —— 界面会据此写"这是模板" ✓。
    悄悄冒充 AI 的方案是**不可接受**的 ✗。
    """
    return ActionPlanDraftResponse(
        title=(message.strip()[:80] or "下一步"),
        reason="AI 这次没有生成成功，这是保底模板 —— 你可以直接改，或者重试一次。",
        minimum_plan="先完成最小的一步",
        fallback_plan="如果状态不好，先投入 15 分钟，然后停下来",
        steps=[ActionStep(title="明确下一步，然后开始", minutes=15, priority="high")],
        source="template",
    )


def generate_draft(message: str) -> ActionPlanDraftResponse:
    """按"你想做什么 + 今天的真实状态"出一份草稿 ✓（**不入库** ✗）。"""
    try:
        state = json.dumps(get_today_context(), ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - 状态拼不出来不该让整个功能失效
        logger.warning("行动计划：读今天的真实状态失败，只按输入生成", exc_info=True)
        state = "（读不到今天的真实状态 ✓）"

    prompt = load_prompt("action_plan.md").replace("{state}", state[:_CONTEXT_CHARS])
    user_turn = f"{prompt}\n\n## 他说的\n{message}"

    try:
        raw = factory.get_provider("chat").chat(
            [{"role": "user", "content": user_turn}],
            json_mode=True,
        )
        data = json.loads(raw)
        plan = ActionPlanCreate.model_validate(data)
    except (ExternalServiceError, ValueError, TypeError) as exc:
        # ⚠️ 只吞**这几种** ✓ —— 别的 bug 要让它们炸出来 ✗
        logger.warning("行动计划生成失败，回退保底模板：%s", exc)
        return _template(message)
    except KeyError as exc:
        logger.warning("行动计划生成结果字段缺失，回退保底模板：%s", exc)
        return _template(message)

    return ActionPlanDraftResponse(**plan.model_dump(), source="ai")
