"""调度器（DEVELOPMENT_PLAN v2.0 · S8/S9）—— **不含业务**。

它只做一件事：按固定间隔叫一次入口流程
（``workflows.proactive.tick()`` + ``workflows.baseline.scan()``
+ ``workflows.extraction.extract_pending()``）。
"该不该发、发什么"全在 workflows 层，所以定时机制可以整体替换
（以后换成系统任务或常驻服务时，业务代码一行都不用改）。

⚠️ 已知限制（方案 §6 明确接受）：**应用关着时不发。**

第一次 tick 发生在 ``interval_seconds`` **之后**，而不是启动瞬间——
这样短命的进程（尤其是测试）永远不会被 tick 波及。
"""

import threading

from app.workflows import baseline, extraction, impression, proactive

_stop = threading.Event()
_thread: threading.Thread | None = None


def run_once() -> None:
    """一次完整的走查：主动联系 + 基线检测 + 记忆抽取 + **印象归纳**。

    手动端点：``POST /api/v1/proactive/tick``（前两件）、
    ``POST /api/v1/memory/extract``、``POST /api/v1/memory/impression-generate``。

    后两件挂在这里、而不是聊天路径上，是因为模型调用可能耗时，
    不应阻塞聊天请求。
    """
    proactive.tick()
    baseline.scan()
    extraction.extract_pending()
    impression.generate_pending()


def _loop(interval_seconds: int) -> None:
    # 先等一个完整间隔：进程刚起来时不做任何主动联系
    while not _stop.wait(interval_seconds):
        try:
            run_once()
        except Exception:
            # 主动联系永远不该把应用搞崩。这里刻意吞掉：
            # 她的"惦记"失败一次没关系，应用起不来才是大事。
            continue


def start_scheduler(interval_seconds: int) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    _stop.clear()
    _thread = threading.Thread(
        target=_loop,
        args=(interval_seconds,),
        name="life-ai-proactive",
        daemon=True,
    )
    _thread.start()


def stop_scheduler() -> None:
    global _thread
    _stop.set()
    _thread = None


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
