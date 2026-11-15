"""自动记忆抽取。

把用户说过的话交给「硬核」模型档拆成 L2 观察。抽取仍由调度器异步执行，
并严格核对模型给出的 source_text；核不到原文的观察整条丢弃。
"""

import json
import logging
from datetime import datetime

from app.core.config import APP_TIMEZONE
from app.core.exceptions import ExternalServiceError
from app.core.prompt_loader import load_prompt
from app.core.time_utils import to_utc_iso, utc_to_local_iso, utc_now
from app.database.connection import get_connection
from app.llm import factory
from app.schemas.memory import ObservationCreate
from app.services import image_service, memory_service, tag_suggestion_service

logger = logging.getLogger(__name__)

# 一次最多抽多少条消息，避免单批上下文过长。
EXTRACT_BATCH_SIZE = 10


def pending_messages(limit: int = EXTRACT_BATCH_SIZE) -> list[dict]:
    """还没抽取过的用户消息，最旧优先。

    用**每条消息自己的** ``metadata_json.extracted`` 标记，而不是
    "上次抽到哪个 id"这种游标 —— 标记是幂等的，
    中途失败重跑不会漏掉中间那些。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, created_at, metadata_json
            FROM messages
            WHERE role = 'user'
            ORDER BY id ASC
            """
        ).fetchall()

    pending: list[dict] = []
    for row in rows:
        metadata = {}
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = {}
        if metadata.get("extracted"):
            continue

        image = metadata.get("image")
        pending.append(
            {
                "id": row["id"],
                "content": row["content"],
                "created_at": row["created_at"],
                # 带图的消息要被"记住"，得先把图读成文字（见 `_describe_image`）✓
                "image": image if isinstance(image, dict) else None,
                "image_text": str(metadata.get("image_text") or ""),
            }
        )
        if len(pending) >= limit:
            break

    return pending


def _render_batch(messages: list[dict], tags: list[dict]) -> str:
    """把待抽取的消息 + **可用标签清单**拼给模型。

    **必须带每条消息的当地时间** —— 否则原话里的"昨天""上周"模型没法换算，
    只能原样抄进 content，过两天这条记忆就读不懂了。

    **必须带标签清单** —— 设计说"你新建的标签她能直接用"（§6.4），
    而且她**只能从清单里挑**（不能自己造标签，那是用户的分类权）。
    """
    lines = []
    for message in messages:
        local = utc_to_local_iso(message["created_at"], APP_TIMEZONE)
        line = f"[{message['id']}] ({local}) {message['content']}"
        # ★ **图里的内容也算这条消息的一部分** ——
        # 否则照片、截图、课表这类消息对记忆库等于不存在：他看得见，但看完就忘 ✗。
        # 这段文字是我们自己存下来的（可核对 ✓），所以它能当出处 ✓。
        image_text = message.get("image_text") or ""
        if image_text:
            line += f"\n　（这张图里的内容：{image_text}）"
        lines.append(line)

    if tags:
        tag_lines = "\n".join(
            f"[{tag['id']}] {tag['name']}（{tag['dimension']}）" for tag in tags
        )
    else:
        tag_lines = "（现在一个标签都没有 —— 那就所有 `tag_ids` 都留空数组）"

    return (
        "可用的标签清单（`tag_ids` **只能从这里面挑**；"
        "挑不到合适的就留空，**不许自己造新标签**）：\n"
        f"{tag_lines}\n\n"
        "以下是用户说过的话。每条前面的**编号和时间**是给你对位、换算用的，"
        "**不要写进 content，也不要写进 source_text**：\n\n" + "\n\n".join(lines)
    )


def _parse(text: str) -> tuple[list[dict], list[dict]]:
    """解析模型返回的 JSON，返回 ``(observations, suggested_tags)``。

    `json_mode` 只是**强约束**、不是保证：它仍可能返回 `{}`、
    缺字段、observations 不是数组。所以这里逐层设防。
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return [], []
    if not isinstance(data, dict):
        return [], []

    raw_observations = data.get("observations")
    observations = [
        item
        for item in (raw_observations if isinstance(raw_observations, list) else [])
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]

    raw_suggestions = data.get("suggested_tags")
    suggestions = [
        item
        for item in (raw_suggestions if isinstance(raw_suggestions, list) else [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]

    return observations, suggestions


def _tag_ids_of(item: dict, valid_tag_ids: set[int]) -> list[int]:
    """取这条观察要挂的标签，**过滤掉清单外的 id**。

    和"出处"一样：**不信任模型传回来的编号** —— 它可能编一个不存在的 tag_id。
    """
    raw = item.get("tag_ids")
    if not isinstance(raw, list):
        return []

    result: list[int] = []
    for value in raw:
        try:
            tag_id = int(value)
        except (TypeError, ValueError):
            continue
        if tag_id in valid_tag_ids and tag_id not in result:
            result.append(tag_id)
    return result


def _locate(quoted, messages: list[dict]) -> dict | None:
    """把模型给的「出处」核回原文，返回**那条消息**。

    **不信任模型传的引用**：它可能改写、拼接、甚至编造一段"原话"。
    所以只在**真实消息**里找它，找不到就说明这条观察没有出处 ——
    按不变量 5，它不该存在。

    返回整条消息（而不只是字符串），是因为观察的时间要用
    **那条消息自己的时间**，不能用"now"或批次里第一条的时间。
    """
    if not quoted or not str(quoted).strip():
        return None

    needle = str(quoted).strip()
    for message in messages:
        if needle in message["content"]:
            return message
        # ★ 图里的文字**也是出处**：那是我们自己读出来、存下来的文字 ✓，
        # 不是模型现编的 —— 所以核得到就作数 ✓。
        if needle in (message.get("image_text") or ""):
            return message
    return None


def _describe_image(image: dict) -> str | None:
    """用**视觉**那一档把图读成文字。读不出来返回 ``None``。

    ⚠️ 这就是设计里那条管道：**图片 → 文字 → L1 → 正常抽取** ✓。
    "图能被看见"和"图能被记住"是两件事 —— 没有这一步，他看完就忘 ✗。

    ⚠️ 走的是 `vision` 任务，和聊天那条路分开 ✓：
    使用独立的 `vision` 模型档，默认模型支持视觉输入。

    失败**不抛异常** —— 一张图读不出来，不该让整批记忆抽取挂掉 ✓。
    它只是没写 `image_text`，调度器下一个 tick 会再试 ✓。
    """
    try:
        data_url = image_service.as_data_url(image)
    except OSError:
        return None
    if not data_url:
        return None

    provider = factory.get_provider("vision")
    builder = getattr(provider, "build_image_message", None)
    if builder is None:
        # ⚠️ **必须留痕。** 静默跳过会让"图为什么没进记忆"完全无从排查 ✗。
        #
        # 写这一条的直接原因：我自己的测试假 provider 没有这个方法，
        # 于是四条测试**静默地失败在断言上**，而不是失败在原因上 ——
        # 而日志里其实早就写着原因了 ✓。
        logger.warning(
            "vision provider %s 认不得图片，这条消息的图不会被读成文字",
            getattr(provider, "name", "?"),
        )
        return None

    message = builder("这张图里有什么？", data_url)
    if message is None:
        return None

    try:
        text = provider.chat(
            [
                {"role": "system", "content": load_prompt("vision_ocr.md")},
                message,
            ],
            temperature=0,
        )
    except ExternalServiceError:
        return None

    return (text or "").strip()[:600] or None


def _confidence_of(item: dict) -> float:
    try:
        value = float(item.get("confidence", 0.7))
    except (TypeError, ValueError):
        return 0.7
    return min(1.0, max(0.0, value))


def _update_metadata(message_id: int, **fields) -> None:
    """给一条消息的 `metadata_json` 打补丁（读-改-写）。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return

        metadata = {}
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = {}
        metadata.update(fields)

        conn.execute(
            "UPDATE messages SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False), message_id),
        )


def _set_extracted(message_id: int, extracted: bool) -> None:
    _update_metadata(message_id, extracted=extracted)


def _set_image_text(message_id: int, text: str) -> None:
    """把图读出来的文字**存回那条消息**。

    存回去有两个用处：

    1. **只读一次** —— 视觉调用不便宜，重抽不该重新读图 ✓
    2. 它成了这条消息的**出处** —— `_locate()` 能核到它 ✓（不变量 5 ✓）
    """
    _update_metadata(message_id, image_text=text)


def unmark_extracted(message_id: int) -> None:
    """把一条消息标回"还没抽取"。

    用途：**编辑之后让他按新版本重新记**（产品需求）。

    ⚠️ 这里**不同步重抽**：模型调用在后台执行，
    会把编辑请求挂住。交给调度器下一个 tick 接手，最多几分钟。

    注意旧的原话**仍然留在 L1**（L1 只追加）——
    所以档案里会同时有旧版和新版，那是对的：档案记录的是"你先后说过什么"。
    """
    _set_extracted(message_id, False)


def extract_pending(
    limit: int = EXTRACT_BATCH_SIZE,
    moment: datetime | None = None,
) -> dict:
    """抽一批。返回做了什么（给 API / 日志看）。"""
    pending = pending_messages(limit)
    if not pending:
        return {
            "checked_messages": 0,
            "created_observations": 0,
            "dropped_without_source": 0,
            "described_images": 0,
            "model": None,
            "suggested_tags": 0,
        }

    # ★ **先把图读成文字**（一次，存在消息上）——
    # "被看见"不等于"被记住"，这一步才是那一下 ✗→✓。
    described = 0
    for message in pending:
        if not message["image"] or message["image_text"]:
            continue
        image_text = _describe_image(message["image"])
        if image_text:
            _set_image_text(message["id"], image_text)
            message["image_text"] = image_text
            described += 1

    # 标签清单：设计说"你新建的标签她能直接用"（§6.4）。
    # 冷启动由 migration 009 预置的标签兜住 —— 所以这里不会是空的。
    tags = memory_service.list_tags()
    valid_tag_ids = {tag["id"] for tag in tags}

    provider = factory.get_provider("extract")
    text = provider.chat(
        [
            {"role": "system", "content": load_prompt("extract.md")},
            {"role": "user", "content": _render_batch(pending, tags)},
        ],
        temperature=0.2,
        json_mode=True,
    )

    observations, suggestions = _parse(text)

    created = 0
    dropped = 0
    first_observation_id: int | None = None

    for item in observations:
        message = _locate(item.get("source_text"), pending)
        if message is None:
            # 核不到出处 → 丢掉。**这是不变量 5 的机械保证**，
            # 不是靠"提示词里写了要引用原文"。
            dropped += 1
            continue

        # 观察时间用**那条消息自己的时间**，不是 now，也不是批次里第一条 ——
        # 否则一批旧消息会被整批记成"今天"，时间轴和历史日期全错。
        observed_at_local = utc_to_local_iso(message["created_at"], APP_TIMEZONE)
        observation = memory_service.create_observation(
            ObservationCreate(
                content=str(item["content"]).strip(),
                source_text=str(item["source_text"]).strip(),
                observed_at_local=observed_at_local,
                confidence=_confidence_of(item),
                tag_ids=_tag_ids_of(item, valid_tag_ids),
            ),
            source_type="chat",
            # 谁衍生了我 —— 删这条消息时要能算出"影响范围"
            message_id=message["id"],
        )
        if first_observation_id is None:
            first_observation_id = observation["id"]
        created += 1

    # 匹不上任何标签时，她**提建议**而不是硬塞（裁决 R7）。
    # 建议挂在这一批的第一条观察上 —— 这就是 R8 要的"可追溯"。
    suggested = 0
    for item in suggestions:
        recorded = tag_suggestion_service.suggest(
            name=str(item.get("name", "")),
            dimension=str(item.get("dimension", "")),
            reason=str(item.get("reason") or "").strip() or None,
            source_observation_id=first_observation_id,
        )
        if recorded is not None:
            suggested += 1

    for message in pending:
        _set_extracted(message["id"], True)

    return {
        "checked_messages": len(pending),
        "created_observations": created,
        "dropped_without_source": dropped,
        "described_images": described,
        "model": getattr(provider, "model", None),
        "suggested_tags": suggested,
    }
