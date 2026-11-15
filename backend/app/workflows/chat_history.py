"""聊天记录的编辑操作：删除 / 编辑 / 重新生成 / 回滚。

## 核心设计决定

**删聊天记录 ≠ 让他忘掉。**

抽取时你的原话被**复制**进了 L1 ``raw_entries``（并且指向它来自哪条消息），
所以删掉一条消息之后，他记住的事实、以及那条事实的出处，**都还在**。

这不是漏洞，是有意的：否则记忆会退化成"没有出处的断言"（不变量 5）。
但它必须**被说出来** —— 否则你会以为删了就等于他没听过，而事实相反。

所以这里每个破坏性操作都：

1. **先算影响范围**（这条消息衍生出了哪些事实）
2. **默认只动聊天记录**
3. 把"连带删记忆"交给你显式选择

## 为什么连带删除不直接写 SQL

逐条走 ``memory_service.delete_observation()`` —— 它会在
"这是某条判断的最后一条依据"时**拒绝**（不变量 6：判断不能被删）。
这里不绕过它，把被拒的如实报回去让你知道。
"""

from app.core.exceptions import DomainValidationError
from app.services import chat_service, memory_service
from app.workflows import extraction


def memory_from_message(message_id: int) -> list[dict]:
    """这条消息衍生出了哪些事实（界面在删除前用它提示你）。"""
    return memory_service.observations_from_message(message_id)


def _delete_derived(message_ids: list[int], purge_archive: bool = False) -> dict:
    """连带删掉这些消息衍生的事实；``purge_archive`` 时连**档案**一起抹。

    ⚠️ **必须在删消息之前调用**：``raw_entries.message_id`` 是
    ``ON DELETE SET NULL``，消息一删，链接就断了，到时候再也查不出影响范围。

    档案（L1 出处）只在**没有任何观察引用**它时才抹得掉 ——
    见 ``memory_service.delete_raw_entry_if_unreferenced()``。
    被拒的观察（某条判断的最后一条依据）会连带保住它那份原话。
    """
    deleted = 0
    refused: list[dict] = []
    entry_ids: list[int] = []

    for message_id in message_ids:
        for observation in memory_service.observations_from_message(message_id):
            entry_ids.append(observation["raw_entry_id"])
            try:
                memory_service.delete_observation(observation["id"])
                deleted += 1
            except DomainValidationError as exc:
                refused.append(
                    {"observation": observation["content"], "reason": str(exc)}
                )

    purged = 0
    if purge_archive:
        for entry_id in dict.fromkeys(entry_ids):
            if memory_service.delete_raw_entry_if_unreferenced(entry_id):
                purged += 1

    return {
        "deleted_observations": deleted,
        "refused": refused,
        "purged_raw_entries": purged,
    }


def _impact(message_ids: list[int]) -> dict:
    """影响范围预览：会波及哪些事实（不删任何东西）。"""
    affected: list[dict] = []
    for message_id in message_ids:
        for observation in memory_service.observations_from_message(message_id):
            affected.append(
                {"id": observation["id"], "content": observation["content"]}
            )
    return {"affected_observations": affected}


def impact_of_deleting(message_id: int) -> dict:
    """删这条消息会影响哪些记忆。"""
    chat_service.get_message(message_id)  # 不存在 → 404
    return _impact([message_id])


def impact_of_rollback(message_id: int) -> dict:
    """回滚到这条消息会影响哪些记忆（它之后的一切）。"""
    chat_service.get_message(message_id)
    return _impact([m["id"] for m in chat_service.messages_after(message_id)])


def delete_message(
    message_id: int,
    with_memory: bool = False,
    purge_archive: bool = False,
) -> dict:
    """删一条消息。默认**只删聊天记录**，记忆留着。"""
    chat_service.get_message(message_id)

    result: dict = {"deleted_messages": 1}
    if with_memory:
        result.update(
            _delete_derived([message_id], purge_archive=purge_archive)
        )

    chat_service.delete_message(message_id)
    return result


def rollback(
    message_id: int,
    with_memory: bool = False,
    purge_archive: bool = False,
) -> dict:
    """回滚到某条消息：**保留它和它之前的一切**，删掉之后的。

    ⚠️ 这**只动聊天记录**。他不会因此忘掉那段时间 ——
    L2/L3 是独立的，那才是"遗忘"，是另一个动作。
    """
    chat_service.get_message(message_id)
    doomed = [message["id"] for message in chat_service.messages_after(message_id)]

    result: dict = {"deleted_messages": len(doomed)}
    if with_memory:
        result.update(_delete_derived(doomed, purge_archive=purge_archive))

    chat_service.delete_messages_after(message_id)
    return result


def edit_message(
    message_id: int,
    content: str,
    with_memory: bool = False,
    purge_archive: bool = False,
) -> dict:
    """改一条消息的正文。

    ``with_memory=False``（默认）—— **只改聊天记录，不动 L1 出处。**
    L1 的定义是"你当时真的说过的话"：改写它会让人物记忆链失去真实性
    （一条事实"背了一小时"引用一句写着"三小时"的话）。

    ``with_memory=True`` —— **让他按新版本重新记**：
    删掉这条消息衍生的事实，再把它标回"待抽取"，调度器几分钟内用新版本重抽。

    两件事有意保留：

    - **旧的原话仍在 L1**（L1 只追加）。档案里会同时有旧版和新版 ——
      这是对的：档案记录的是"你先后说过什么"，不是"你最后认可什么"。
    - **不同步重抽**。模型调用可能耗时，会把编辑请求挂住。
    """
    message = chat_service.get_message(message_id)
    if message["role"] != "user":
        raise DomainValidationError("只能编辑你自己发过的消息。")

    result: dict = {}
    if with_memory:
        # 先删旧事实（此时链接还在）
        result.update(
            _delete_derived([message_id], purge_archive=purge_archive)
        )

    updated = chat_service.update_message_content(message_id, content)

    if with_memory:
        extraction.unmark_extracted(message_id)

        parts = ["已按新版本重记：旧的事实删掉了"]
        if result.get("purged_raw_entries"):
            parts.append("旧的原话也从档案里抹掉了")
        parts.append("这条消息会在几分钟内被重新抽取")
        note = "，".join(parts) + "。"

        if result.get("refused"):
            note += (
                f"（有 {len(result['refused'])} 条没能删掉 ——"
                "它们是某条判断的最后一条依据，所以那份原话也留着。）"
            )
    else:
        note = "改的是聊天记录。你当时说的原话仍留在他记忆的出处里（不改写历史）。"

    return {"message": updated, "note": note, **result}
