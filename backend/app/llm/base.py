from abc import ABC, abstractmethod
from collections.abc import Iterator


class LLMProvider(ABC):
    """模型提供方（DEVELOPMENT_PLAN v2.0 · MODEL_USAGE_SPEC §1.3）。

    **同步实现。** 现有 service 层全是同步的，所以这里不引入 async，
    也不引入 `openai` SDK（见 MODEL_USAGE_SPEC §6）——那是较大改动且无收益。

    注意 ``messages`` 的类型是 ``list[dict]`` 而不是 ``list[dict[str, str]]``：
    视觉要把 ``content`` 换成 block 数组（§3.1），值不再是纯字符串。
    """

    #: 供日志与诊断使用的短名。子类可以覆盖。
    name = "unknown"

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """一次性对话。

        ``json_mode=True`` 表示要求模型返回 JSON（记忆抽取、印象归纳使用）。
        """
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """能力探测：没配置必要凭据时返回 ``False``。默认 ``True``。
        """
        return True

    def build_image_message(self, text: str, data_url: str) -> dict | None:
        """构造一条**带图片**的 user 消息。**认不得图片就返回 ``None``。**

        图片消息使用 OpenAI 兼容的多模态 block 结构。
        当前生产 provider 支持该结构；测试或扩展 provider 可以返回 ``None``。

        ⚠️ **刻意不是 `@abstractmethod`**：测试里的 FakeProvider 是鸭子类型的，
        加抽象方法会把它们全打挂（见 `chat_stream` 的同款注释）。
        所以调用方用 ``getattr(provider, "build_image_message", None)``。
        """
        return None

    def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
    ) -> Iterator[str]:
        """流式输出（S6）。

        **默认实现退化成"一次性吐出整段"**：这样所有已有 provider 都不必改，
        测试里的 FakeProvider 也不会因为新增抽象方法而失败
        （它们是鸭子类型的，加 abstractmethod 会直接把它们打挂）。
        真正的逐字流式由具体 provider 覆盖 —— 见 ``DeepSeekProvider.chat_stream``。
        """
        yield self.chat(messages, temperature)
