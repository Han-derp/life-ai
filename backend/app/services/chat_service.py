import json
import logging
import re
from collections.abc import Iterator

from app.ai.context.assembler import ContextPlan, plan_for_request
from app.core.config import APP_TIMEZONE, DEEPSEEK_MODEL
from app.core.exceptions import (
    DomainValidationError,
    ExternalServiceError,
    NotFoundError,
)
from app.core.time_utils import (
    current_time_note,
    to_utc_iso,
    utc_now,
    utc_to_local_iso,
)
from app.database.connection import get_connection
from app.llm import factory
from app.services import context_exclusion_service, image_service, tone_service


logger = logging.getLogger(__name__)

# 对话使用「陪聊」模型档；代理保证设置变更后立即生效。
_provider = factory.current_provider("chat")


def _default_conversation_id() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM conversations ORDER BY id ASC LIMIT 1").fetchone()
    if row is None:
        raise NotFoundError("默认会话不存在，请重新初始化数据库。")
    return int(row["id"])


def _message_row_to_dict(row) -> dict:
    metadata = None
    if row["metadata_json"]:
        try:
            metadata = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            metadata = {"raw": row["metadata_json"]}
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "model": row["model"],
        "metadata": metadata,
    }


def list_messages(limit: int = 100) -> list[dict]:
    conversation_id = _default_conversation_id()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (conversation_id, max(1, min(limit, 500))),
        ).fetchall()
    # ⚠️ 导入的清单**不进聊天页** ✓ —— 它是一批数据，不是一次对话 ✓
    #（否则聊天页会出现一个几百行的气泡 ✗）
    return [
        item
        for item in (_message_row_to_dict(row) for row in rows)
        if not _is_imported(item)
    ]


#: 导入清单的上限。它不是聊天，是"一次性喂进去的一批事实"。
MAX_IMPORT_LINES = 200
MAX_IMPORT_CHARS = 20_000


def _is_imported(message: dict) -> bool:
    return bool((message.get("metadata") or {}).get("imported"))


def import_notes(text: str) -> dict:
    """把一份**清单**当成"你说过的话"存进来（记忆导入）。

    ## 为什么这样做，而不是直接写事实表

    不变量 5 要求「**每条事实都能追回一句话**」✓ ——
    手动塞进事实表的东西**没有出处** ✗ → 那就是给记忆库开了一个
    "可以无中生有"的后门 ✓。

    所以导入 = **写一条用户消息** ✓ → 之后走**正常抽取流程** ✓ →
    事实、出处、时间、标签全都自动对上 ✓✓。

    ## ⚠️ 它必须被排除在两处之外

    这可能是一份几百行的清单 ✓，**不是一次对话** ✓：

    - 不进**发给模型的最近聊天** ✓（否则每次都撑爆 4000 的预算 ✗）
    - 不进**聊天页的消息列表** ✓（否则是一个几百行的气泡 ✗）

    **结果在哪看**：关于TA 页 ✓（抽出来的事实会出现在那里 ✓）。
    """
    lines = [
        line.strip()
        for line in (text or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        raise DomainValidationError(
            "没有可导入的内容（每行一条；空行和 # 开头的会跳过）。"
        )
    if len(lines) > MAX_IMPORT_LINES:
        raise DomainValidationError(
            f"一次最多导入 {MAX_IMPORT_LINES} 条（你给了 {len(lines)} 条）"
            "—— 分几次更稳 ✓。"
        )

    body = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))
    content = (
        "【下面这些是我以前记下来的事情，请把它们当成我说过的话，"
        "按正常流程抽成事实】\n" + body
    )
    if len(content) > MAX_IMPORT_CHARS:
        raise DomainValidationError(f"内容太长了（上限 {MAX_IMPORT_CHARS} 字）。")

    message = _save_message("user", content, metadata={"imported": True})
    return {"imported_lines": len(lines), "message_id": message["id"]}


def _save_message(role: str, content: str, model: str | None = None, metadata: dict | None = None) -> dict:
    conversation_id = _default_conversation_id()
    now = to_utc_iso(utc_now())
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, model, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, role, content, model, metadata_json, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _message_row_to_dict(row)


def record_proactive_message(content: str, mechanism: str) -> dict:
    """把「她主动说的」也写进聊天记录（设计裁决 R2③）。

    **必须写进 `messages`，而且必须带标记。** 否则两个后果：
    1. 你在聊天记录里看不到她主动说的话 —— 那她"找过你"这件事等于没发生；
    2. 以后分不清"她主动说的"和"回复你说的"。

    标记形如 ``{"source": "proactive", "mechanism": "silence_3d"}``。
    `metadata` 已经由 `_message_row_to_dict()` 暴露给前端，
    所以界面以后可以据此加个「他主动说的」角标。
    """
    return _save_message(
        "assistant",
        content,
        metadata={"source": "proactive", "mechanism": mechanism},
    )


def _mark_message_failed(message_id: int, error: str) -> None:
    metadata = json.dumps(
        {"generation_status": "failed", "error": error[:400]}, ensure_ascii=False
    )
    with get_connection() as conn:
        conn.execute(
            "UPDATE messages SET metadata_json = ? WHERE id = ?",
            (metadata, message_id),
        )


#: 历史里最多重新附带几张图。更早的图对当前对话没用了，带上只是白花钱 ——
#: 每张图 base64 之后都有几十上百 KB。
MAX_HISTORY_IMAGES = 2


def _metadata_of(raw) -> dict:
    """解析 ``metadata_json``。坏数据当成空 —— 不该因为一条脏记录崩掉整次请求。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _stamp(created_at, content):
    """给历史消息的内容加上**绝对本地时间**前缀。

    ## 为什么

    用户实测：历史消息**不带时间** ✗ —— 她说"昨天聊的那个"时，
    模型翻到那几条旧消息，**不知道哪一条是"昨天"** ✗
    （原来只有**当前这一句**末尾挂着「（现在：…）」✓）。

    ## 为什么必须是**绝对**时间

    ⚠️ 相对时间（"昨天""3 天前"）**每天都不一样** ✗ ——
    于是**同一批历史**每次请求渲染出不同的字节 ✓ → KV 缓存前缀全废 ✓ → 变慢变贵 ✓。

    绝对时间（`[10-04 23:20]`）对同一条消息**永远一个样** ✓ → 前缀稳定 ✓。
    "这是几天前"交给模型自己算 ✓（当前那句末尾有时间 ✓）。

    ⚠️ 带图的消息 `content` 是**数组**（不是字符串 ✓）—— 那里面没地方放前缀 ✓，原样返回 ✓。
    """
    if not isinstance(content, str) or not created_at:
        return content
    try:
        local = utc_to_local_iso(created_at, APP_TIMEZONE)
    except (TypeError, ValueError):
        return content
    # `2026-10-04T23:24+08:00` → `[10-04 23:24]`
    return f"[{local[5:16].replace('T', ' ')}] {content}"


_STAMP_RE = re.compile(r"^\[\d\d-\d\d \d\d:\d\d\] ")


def strip_stamp(content):
    """把 `_stamp()` 加的前缀去掉。

    ⚠️ **必须做。** `build_context_plan()` 拿"最后一条用户消息"当**检索词** ✓，
    而那批消息现在带着时间戳 ✓ —— 不去掉的话，检索词里会混进 `[10-05 16:04]` ✗
    → 面板检索出来的记忆，和**真实请求**（用的是干净原话 ✓）**对不上** ✗✗。

    那正是"**看到的就是要发的**"这条承诺要防的事 ✓
    （2026-10-05 由 `test_the_preview_matches_the_streaming_path` 当场抓住 ✓）。
    """
    if not isinstance(content, str):
        return content
    return _STAMP_RE.sub("", content)


def _history_items(rows) -> list[dict]:
    """把消息行拼成 provider 要的历史。

    带图的消息 content 变成**数组** —— 图片只能放在 ``user`` 消息里（API 硬要求）。
    只重新附带最近 ``MAX_HISTORY_IMAGES`` 张：再往前的图只是白花钱。

    ⚠️ 每条都过 `_stamp()` 加上时间 ✓ —— 面板渲染的也是这份数据 ✓，
    所以"**看到的就是要发的**"这条承诺自动成立 ✓。
    """
    with_image: set[int] = set()
    budget = MAX_HISTORY_IMAGES
    for row in reversed(rows):
        if _metadata_of(row["metadata_json"]).get("image") and budget > 0:
            with_image.add(row["id"])
            budget -= 1


    items: list[dict] = []
    for row in rows:
        # ⚠️ **导入的清单不进上下文** ✓ —— 它可能几百行，
        # 进了"最近聊天"会把 4000 的预算撑爆 ✗（那是钱 ✓）。
        if (_metadata_of(row["metadata_json"]) or {}).get("imported"):
            continue

        image = _metadata_of(row["metadata_json"]).get("image")
        if (
            image
            and row["id"] in with_image
            and row["role"] == "user"
        ):
            # 消息形状由当前 OpenAI 兼容 provider 构造。
            built = _image_turn(row["content"], image_service.as_data_url(image))
            if built is not None:
                items.append(built)
                continue
        items.append(
            {
                "role": row["role"],
                "content": _stamp(row["created_at"], row["content"]),
            }
        )
    return items


def _raw_history_before(message_id: int, limit: int = 200) -> list[dict]:
    """当前消息之前的聊天记录。

    **不在这里裁剪预算**：S2 起 system 块与最近聊天共用一个总预算，
    由 ``plan_for_request`` 统一决定丢谁（见 DEVELOPMENT_PLAN §1.5 决定 3）。
    这里只做条数上限，避免把整段历史读进内存。
    """
    conversation_id = _default_conversation_id()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, metadata_json, created_at FROM (
                SELECT id, role, content, metadata_json, created_at FROM messages
                WHERE conversation_id = ? AND id < ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (conversation_id, message_id, max(1, min(limit, 1000))),
        ).fetchall()
    return _history_items(rows)


def recent_history(limit: int = 200) -> list[dict[str, str]]:
    """最近聊天。面板预览用：假设"现在再发一条"会带上的历史。"""
    conversation_id = _default_conversation_id()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, metadata_json, created_at FROM (
                SELECT id, role, content, metadata_json, created_at FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (conversation_id, max(1, min(limit, 1000))),
        ).fetchall()
    return _history_items(rows)


# ---------- 聊天记录的编辑（删除 / 编辑 / 重新生成 / 回滚） ----------
#
# 这一层只做**原语**（读一条、删一条、改正文、取某条之后的一切）。
# "删消息要不要连带删记忆"是业务流程，放在 `workflows/chat_history.py` ——
# 因为那要同时碰 chat 和 memory 两个 service。


def get_message(message_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("这条消息不存在。")
    return _message_row_to_dict(row)


def _images_from_rows(rows) -> list[dict]:
    images = []
    for row in rows:
        image = _metadata_of(row["metadata_json"]).get("image")
        if isinstance(image, dict):
            images.append(image)
    return images


def _images_of(message_ids: list[int]) -> list[dict]:
    """取这几条消息带的图片引用。**必须在删行之前调** ✓。"""
    if not message_ids:
        return []
    placeholders = ",".join("?" for _ in message_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT metadata_json FROM messages WHERE id IN ({placeholders})",
            tuple(message_ids),
        ).fetchall()
    return _images_from_rows(rows)


def delete_message(message_id: int) -> None:
    # ⚠️ 先把图片引用取出来 —— 删完行就查不到了 ✓
    images = _images_of([message_id])

    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    if cursor.rowcount == 0:
        raise NotFoundError("这条消息不存在。")

    # 行删了，文件也别留着 —— 否则 storage/images/ 会攒一堆孤儿 ✗
    image_service.delete_many(images)


def update_message_content(message_id: int, content: str) -> dict:
    """改一条消息的正文。**只动 `messages`。**

    L1 `raw_entries` 里那份原话**不跟着改** —— 见 `workflows/chat_history.py`。
    """
    clean = content.strip()
    if not clean:
        raise DomainValidationError("消息不能为空。")

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            (clean, message_id),
        )
        if cursor.rowcount == 0:
            raise NotFoundError("这条消息不存在。")

        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    return _message_row_to_dict(row)


def preceding_user_message(assistant_message_id: int) -> dict | None:
    """这条回复是回给谁的（重新生成要用它重建上下文）。"""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM messages
            WHERE role = 'user' AND id < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (assistant_message_id,),
        ).fetchone()
    return _message_row_to_dict(row) if row is not None else None


def messages_after(message_id: int) -> list[dict]:
    """某条消息**之后**的所有消息（回滚的影响范围）。"""
    conversation_id = _default_conversation_id()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id = ? AND id > ?
            ORDER BY id ASC
            """,
            (conversation_id, message_id),
        ).fetchall()
    return [_message_row_to_dict(row) for row in rows]


def delete_messages_after(message_id: int) -> int:
    """删掉某条消息之后的一切（回滚）。返回删了几条。"""
    conversation_id = _default_conversation_id()
    with get_connection() as conn:
        # ⚠️ 先取这一段的图片引用 —— 删完行就查不到了 ✓
        rows = conn.execute(
            "SELECT metadata_json FROM messages "
            "WHERE conversation_id = ? AND id > ?",
            (conversation_id, message_id),
        ).fetchall()

        cursor = conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
            (conversation_id, message_id),
        )

    image_service.delete_many(_images_from_rows(rows))
    return cursor.rowcount


def regenerate_reply(message_id: int) -> dict:
    """重新生成回复。同一个入口，两种用法：

    - 传 **assistant** 消息 → 重做那一条（**替换**掉旧的）
    - 传 **user** 消息 → 为它生成一条新回复

    后者是**失败重试**唯一能走的路：生成失败时**根本没有 assistant 消息**
    可重做，只有那条被标成 ``generation_status: failed`` 的用户消息。

    ⚠️ 顺序：**先生成、成功了再删旧的**。反过来的话，生成失败时
    你会连原来那条也丢掉 —— 一次失败变成两次损失。
    """
    target = get_message(message_id)

    if target["role"] == "user":
        return _generate_reply(target)

    if target["role"] != "assistant":
        raise DomainValidationError("只能对聊天消息重新生成。")

    user_message = preceding_user_message(message_id)
    if user_message is None:
        raise DomainValidationError("这条回复之前没有用户消息，无法重新生成。")

    # 上下文只取决于那条用户消息（旧回复在它之后，不在上下文里），
    # 所以"先生成"不会看到旧回复。
    new_message = _generate_reply(user_message)
    delete_message(message_id)
    return new_message


def _text_of(content) -> str:
    """从一条消息的 content 里取出**纯文字**。

    ⚠️ 带图的消息 content 是 **block 数组**（`[{"type":"text",...}, {"type":"image_url",...}]`）
    —— 直接拿它当检索词会炸（`list` 没有 `.strip()` ✗）。
    这个坑真的发生了：删除一条消息之后，"最后一条用户消息"变成了带图那条，
    预览接口就 500 了。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def build_context_plan() -> ContextPlan:
    """当前会发送的上下文计划（S2 面板用；只读，不写任何数据）。

    ⚠️ 预览用**最后一条用户消息**当检索词 —— 因为真实请求用的是"你正要发的那句"，
    而预览时它还不存在。这是最接近的代理；不这样做的话，
    面板显示的记忆会和实际发出的**不是同一批**，而面板的整个承诺就是"看到的就是要发的"。

    ⚠️ 取文字必须走 `_text_of()`：那条消息可能带图（content 是数组）。
    """
    history = recent_history()
    last_user = next(
        (
            # ⚠️ **先剥掉时间戳** ✓ —— 检索词必须是干净原话 ✓，
            # 否则面板和真实请求会对不上 ✗（2026-10-05 那次事故 ✓）。
            strip_stamp(_text_of(item["content"]))
            for item in reversed(history)
            if item["role"] == "user"
        ),
        None,
    )
    return plan_for_request(history, query=last_user)


def last_usage() -> dict | None:
    """上一轮真实请求的 usage（含 prompt_cache_hit_tokens）。"""
    usage = getattr(_provider, "last_usage", None)
    return usage if isinstance(usage, dict) else None


def _with_current_time(text: str) -> str:
    """把「现在几点」附在用户那句话后面。

    ⚠️ **绝不能放进 system prompt。** system prompt 一改，它**后面的一切**
    （包括整段聊天历史）都会因为前缀不匹配而失去 KV 缓存 ——
    实测缓存命中能省 20–100 倍费用。而「最新那条用户消息」每次请求
    本来就是新的，挂在这里零成本。

    落库的仍然是原文（``_save_message`` 收的是 ``clean``），
    所以历史里不会越堆越多「（现在：…）」。
    """
    return f"{text}\n\n{current_time_note(APP_TIMEZONE)}"


def _image_metadata(data_url: str | None) -> dict | None:
    """把上传的 data URL 落盘，返回要写进 ``metadata_json`` 的字典。

    没有图返回 ``None``（``_save_message`` 会把 metadata 存成 NULL）。
    """
    if not data_url:
        return None

    # 图片在本版本由配置的 OpenAI 兼容视觉模型处理。
    return {"image": image_service.save_data_url(data_url)}


def _image_turn(text: str, data_url: str) -> dict | None:
    """问**当前 provider** 要一条带图的消息。它认不得就返回 ``None``。

    ⚠️ 用 `getattr` 而不是直接调：测试里的 FakeProvider 是鸭子类型的，
    没有这个方法（设计裁决 Q5 的注释里写了为什么不做成抽象方法）。
    """
    if not data_url:
        return None

    builder = getattr(_provider, "build_image_message", None)
    if builder is None:
        # provider 认不得图片 → 退化成纯文本。**但必须留下痕迹**：
        # 静默丢图会让"他为什么没看见我的图"完全无从排查 ✗。
        logger.warning(
            "provider %s 不支持带图消息，本次按纯文本发送",
            getattr(_provider, "name", "?"),
        )
        return None

    return builder(text, data_url)


def _user_turn(text: str, image: dict | None) -> dict:
    """「用户这一句」在 provider 里的形状。带图时由 provider 自己决定。

    ⚠️ 图片只能放在 **user** 消息里 —— 放 system 或 assistant 会被 API 拒绝（400）。
    """
    body = _with_current_time(text)
    if not image:
        return {"role": "user", "content": body}

    built = _image_turn(body, image_service.as_data_url(image))
    # provider 认不得图片 → **退回纯文本**，绝不按别的 API 的形状硬发 ✗
    return built or {"role": "user", "content": body}


def _generate_reply(user_message: dict) -> dict:
    """按一条**既有的**用户消息生成回复。

    `send_chat` 和"重新生成"**共用这一段** —— 否则两条路发出去的上下文
    可能不一样，"为什么两次回答不同"就永远解释不清了。
    """
    clean = user_message["content"]
    image = (user_message.get("metadata") or {}).get("image")
    # query = 这次要检索什么：就是他刚说的那句（FTS5 相关度检索记忆）
    plan = plan_for_request(_raw_history_before(user_message["id"]), query=clean)
    # exclude_once 只对本次请求生效，组装完成后立即消费掉。
    context_exclusion_service.consume_once_ids()

    messages = [{"role": "system", "content": plan.system_prompt}]
    messages.extend(plan.history)
    messages.append(_user_turn(clean, image))

    reply = _provider.chat(messages)
    return _save_message(
        "assistant",
        _without_assistant_tone(reply),
        model=DEEPSEEK_MODEL,
        metadata={"generation_status": "ok", "usage": last_usage()},
    )


def _without_assistant_tone(reply: str) -> str:
    """删掉客服腔（逐句）。

    ⚠️ **只作用于非流式这条路**（`send_chat` / 重新生成）。
    流式那条路改不了：字是一个一个吐出去的，等拿到整句时用户已经看见了 ✗。
    要让它也生效只能**按句缓冲**（攒够一句、清一遍、再发）——
    那会牺牲逐字的手感，是个设计取舍，没自己做 ✗。

    全部被删时 `scrub` 会返回原文 —— 那说明整条都在打客服腔（模型不对 ✗），
    这时必须留痕，否则你只会觉得"她今天有点怪"。
    """
    scrubbed = tone_service.scrub_assistant_tone(reply)
    if scrubbed == reply and any(
        tell in reply for tell in tone_service.ASSISTANT_TELLS
    ):
        logger.warning("整条回复都是客服腔，已原样保留（检查当前模型与提示词配置是否合适）")
    return scrubbed


def send_chat(content: str, image: str | None = None) -> dict:
    clean = content.strip()
    if not clean and not image:
        raise DomainValidationError("要么说点什么，要么发张图。")

    user_message = _save_message("user", clean, metadata=_image_metadata(image))
    try:
        assistant_message = _generate_reply(user_message)
        return {"user_message": user_message, "assistant_message": assistant_message}
    except ExternalServiceError as exc:
        _mark_message_failed(user_message["id"], str(exc))
        raise


# ---------- 流式对话（S6） ----------


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_chat(content: str, image: str | None = None) -> Iterator[str]:
    """流式对话（S6）。产出 SSE 数据行，由 API 层包成 ``StreamingResponse``。

    对应 S6 的三条完成判据：

    1. **尽早出字**：先落用户消息、再组装上下文，然后立刻开始产出 delta。
    2. **流式内容与落库内容一致**：整段回复只写一次，内容就是所有 delta
       拼起来的原文，不做二次加工。
    3. **中断不写坏数据**：客户端断开时（``GeneratorExit``）把已收到的部分
       以 ``generation_status: "interrupted"`` 落库，不留半条"完整回复"；
       正常结束时不会重复写。
    """
    clean = content.strip()
    if not clean and not image:
        yield _sse({"type": "error", "detail": "要么说点什么，要么发张图。"})
        return

    user_message = _save_message("user", clean, metadata=_image_metadata(image))

    collected: list[str] = []
    saved = False

    try:
        # query = 这次要检索什么（FTS5 相关度）。
        # ⚠️ 这一行曾经**漏了** `query=clean` —— 后果是"真实聊天里相关度检索
        # 一次都没跑过"，而且面板（`build_context_plan()` 传了 query）
        # 显示的记忆和实际发出去的可能不是同一批 ✗。
        # 两条路必须逐字节一致，见 tests/service/test_chat_query_wiring.py。
        plan = plan_for_request(
            _raw_history_before(user_message["id"]), query=clean
        )
        # exclude_once 只对本次请求生效，组装完成后立即消费掉。
        context_exclusion_service.consume_once_ids()

        messages = [{"role": "system", "content": plan.system_prompt}]
        messages.extend(plan.history)
        messages.append(
            _user_turn(clean, (user_message.get("metadata") or {}).get("image"))
        )

        for chunk in _provider.chat_stream(messages):
            if not chunk:
                continue
            collected.append(chunk)
            yield _sse({"type": "delta", "text": chunk})

        reply = "".join(collected).strip()
        assistant_message = None
        if reply:
            assistant_message = _save_message(
                "assistant",
                reply,
                model=DEEPSEEK_MODEL,
                metadata={"generation_status": "ok", "usage": last_usage()},
            )
            saved = True

        yield _sse(
            {
                "type": "done",
                "content": reply,
                "assistant_message_id": (
                    assistant_message["id"] if assistant_message else None
                ),
            }
        )
    except ExternalServiceError as exc:
        _mark_message_failed(user_message["id"], str(exc))
        yield _sse({"type": "error", "detail": str(exc)})
    except GeneratorExit:
        # 客户端断开（关页面、切走）。落库已收到的部分，但标成 interrupted——
        # 绝不把它当成一次完整回复。
        if not saved:
            partial = "".join(collected).strip()
            if partial:
                _save_message(
                    "assistant",
                    partial,
                    model=DEEPSEEK_MODEL,
                    metadata={
                        "generation_status": "interrupted",
                        "usage": last_usage(),
                    },
                )
        raise
    except Exception as exc:
        # 流一旦开始，就不可能再改 HTTP 状态码了。这里必须兜住，
        # 否则用户只看到一条断掉的连接，不知道为什么。
        # 域内错误在上面已被更具体的分支处理；走到这里多半是配置或编程问题，
        # 所以把异常类型一起带出去，别让它无声无息。
        _mark_message_failed(user_message["id"], str(exc))
        yield _sse({"type": "error", "detail": f"{type(exc).__name__}: {exc}"})
