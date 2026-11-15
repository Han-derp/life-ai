import json
from collections.abc import Iterator

import httpx

from app.core import config
from app.core.config import (
    DEEPSEEK_TEMPERATURE,
    DEEPSEEK_TIMEOUT_SECONDS,
)
from app.core.exceptions import ExternalServiceError
from app.llm.base import LLMProvider


class OpenAICompatProvider(LLMProvider):
    """**任何 OpenAI 兼容接口** —— DeepSeek / MiniMax / 豆包 / 自建都行。

    ⚠️ 它原来叫 `DeepSeekProvider` ✗ —— 但代码从来就不是 DeepSeek 专用的：
    说到底只有 base_url + key + model 三样 ✓。用户要求"在界面上自己填模型" ✓，
    所以改成正名，并把 `DeepSeekProvider` 留成**别名** ✓
    （否则满仓库测试里的 `monkeypatch.setattr(DeepSeekProvider, ...)` 会全断 ✗）。

    `name` 保留为 `"deepseek"`，兼容现有状态与日志字段；自定义 base_url 仍可指向其他 OpenAI 兼容服务。
    """

    name = "deepseek"

    def __init__(self, model: str | None = None, tier: str | None = None) -> None:
        # **刻意不在这里快照 key / base_url / model。**
        # 它们可以在网页的「设置」里改，而改完不该要求重启进程：
        # 所以统一走下面的 property，每次调用都从 config 读"当下值"。
        # （settings_service 保存时会同步更新内存里的 config，不是只改 .env 文件。）
        self._model_override = model
        #: 这一档的名字（roleplay / analysis / vision）。
        #: `None` = 直接用 `DEEPSEEK_*` 那套（旧行为，测试与老配置都走这条）。
        self._tier = tier
        # 最近一次调用的 usage（含 prompt_cache_hit_tokens）。
        # 供 S2 上下文面板显示"本次缓存命中"；还没调用过时为 None。
        self.last_usage: dict | None = None

    def _tier_value(self, field: str) -> str:
        if not self._tier:
            return ""
        return str(config.tier_config(self._tier).get(field) or "")

    @property
    def api_key(self) -> str:
        return self._tier_value("api_key") or config.DEEPSEEK_API_KEY

    @property
    def base_url(self) -> str:
        return self._tier_value("base_url") or config.DEEPSEEK_BASE_URL

    @property
    def model(self) -> str:
        # 优先：factory 按任务/槽位传进来的 → 其次这一档配的 → 最后 DEEPSEEK_CHAT_MODEL。
        return (
            self._model_override
            or self._tier_value("model")
            or config.DEEPSEEK_CHAT_MODEL
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """一次性对话。

        **视觉不需要额外代码**：``messages`` 是原样塞进请求体的，
        所以 content 传 block 数组（``[{type: text}, {type: image_url}]``）就能用。
        唯一约束是**图片只能放在 user 消息里**（放 system / assistant 会 400）。

        ``json_mode`` 走 OpenAI 兼容的 ``response_format=json_object``。
        """
        if not self.api_key:
            raise ExternalServiceError(
                "未配置模型 API Key。请在设置页填写后重试。"
            )
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": DEEPSEEK_TEMPERATURE if temperature is None else temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=DEEPSEEK_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            self.last_usage = data.get("usage")
            return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise ExternalServiceError(
                f"模型服务 API 返回 {exc.response.status_code}：{detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ExternalServiceError(f"模型服务调用失败：{exc}") from exc

    def build_image_message(self, text: str, data_url: str) -> dict | None:
        """OpenAI 形状：``content`` 是 block 数组，图片放在 **user** 消息里。

        ⚠️ 图片**只能**放 user 消息（放 system / assistant 会 400）。
        ``detail: "low"`` 缩到 512×512 —— 更便宜更快（MODEL_USAGE_SPEC §3.1）。
        """
        if not data_url:
            return None

        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": "low"},
                },
            ],
        }

    def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
    ) -> Iterator[str]:
        """逐字流式（S6）。

        DeepSeek 的流是 data-only SSE，以 ``data: [DONE]`` 结束。

        **usage 不需要 `stream_options`**：官方文档写明"最后一个 chunk 一定带
        usage 统计"（挂在 ``[DONE]`` 之前那一块上）。所以这里顺手把它记进
        ``last_usage``，S2 面板的"本次缓存命中"在流式下也不会变空。

        注意最后一个 chunk 的 ``choices[0].delta`` 没有新内容（只带
        ``finish_reason``），所以只在 ``text`` 非空时才产出。
        """
        if not self.api_key:
            raise ExternalServiceError(
                "未配置模型 API Key。请在设置页填写后重试。"
            )
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": DEEPSEEK_TEMPERATURE if temperature is None else temperature,
            "stream": True,
        }
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=DEEPSEEK_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()

                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    usage = parsed.get("usage")
                    if usage:
                        self.last_usage = usage

                    choices = parsed.get("choices") or []
                    if not choices:
                        continue
                    text = (choices[0].get("delta") or {}).get("content")
                    if text:
                        yield text
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise ExternalServiceError(
                f"模型服务 API 返回 {exc.response.status_code}：{detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"模型服务流式调用失败：{exc}") from exc


#: **向后兼容别名。** 满仓库的测试都写 `monkeypatch.setattr(DeepSeekProvider, ...)`，
#: 改名会让它们全断 ✗。名字里的 DeepSeek 是历史遗留 ——
#: 这个类其实是**通用 OpenAI 兼容**客户端 ✓（base_url + key + model 三样）。
DeepSeekProvider = OpenAICompatProvider
