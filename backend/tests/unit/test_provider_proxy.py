"""消费方手里的 provider 是**代理** —— 所以改设置不用重启。

## 这是一笔老债

`chat_service` / `proactive` / `baseline` / `deadline_service` / `review_service`
都在 **import 时**抓了一个 provider 实例 ✗ ——
于是"在设置里换模型 / 换模式"必须**重启后端**才生效，
界面只能老实写一句"请重启" ✓（烦 ✓，而且用户已经碰到过好几次）。

改成抓 `factory.current_provider()`（代理 ✓）之后：代理每次调用都去问
`get_provider()`，也就是"当前配置 + 当前缓存" ✓ ——
所以 `settings_service.save()` 里那次 `reset_cache()` 之后**立刻生效** ✓。
"""

from app.llm import factory


def test_the_proxy_follows_the_current_config(monkeypatch):
    """★ 换配置之后，**别人手里那个代理**立刻指向新实例 ✓。"""
    proxy = factory.current_provider("chat")
    assert proxy.model == factory.get_provider("chat").model

    # 模拟"在设置页里把陪聊那一档换了个模型"：
    # settings_service 会写 .env + 同步内存 + reset_cache() ✓
    monkeypatch.setenv("LLM_TIER_ROLEPLAY_MODEL", "changed-model")
    factory.reset_cache()

    assert proxy.model == "changed-model"


def test_the_proxy_exposes_everything_the_consumers_use():
    """代理要能透传消费方真正用到的那几样。

    （`__getattr__` 一条就够 —— 但这条测试把它**钉住**：
     否则某天有人给代理加了固定属性，那几个就会静默失联 ✗。）
    """
    proxy = factory.current_provider("chat")

    assert isinstance(proxy.name, str)
    assert isinstance(proxy.available, bool)
    assert isinstance(proxy.base_url, str)
    assert proxy.last_usage is None or isinstance(proxy.last_usage, dict)


def test_get_provider_still_returns_a_real_instance():
    """★ `get_provider()` **不许**返回代理。

    测试里有 `isinstance(factory.get_provider("chat"), …)` 这类断言 ✗，
    换成代理会全断 ✓ —— 所以代理只从 `current_provider()` 出。
    """
    from app.llm.deepseek import OpenAICompatProvider

    assert isinstance(factory.get_provider("chat"), OpenAICompatProvider)
