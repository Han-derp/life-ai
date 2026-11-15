"""记忆库：L1 原始资料 / L2 事件观察 / 标签（DEVELOPMENT_PLAN v2.0 · S4）。

这一层是"零记录成本"的落点：事实从聊天里长出来，用户不填表。

**本子步落地的是数据与读写通道**（migration + service + API）。
下面两件事**还没做**，别误以为已经能用：

1. 从聊天里自动抽取 L2 事件（需要 LLM，是 S4 的下一个子步）
2. L3 印象的生成与修订（同类事件累积 3 次 / 同类反例累积 3 次触发）

**不变量 5**：每条观察都必须有出处（``raw_entry_id`` → L1）。
所以 ``create_observation`` 一定会在**同一个事务**里先写 L1 再写 L2——
不允许存在查不到原话的"我记得"。
"""

import json
from datetime import date

from app.core.config import APP_TIMEZONE
from app.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from app.core.time_utils import (
    local_date_of,
    local_datetime_to_utc_iso,
    to_utc_iso,
    utc_now,
    utc_to_local_iso,
)
from app.database.connection import get_connection
from app.schemas.memory import (
    ObservationCreate,
    ObservationTagsUpdate,
    TagCreate,
    TagUpdate,
)

# 一条观察最多挂多少标签。标签太多她自己也记不住，检索反而变差（MEMORY_DESIGN §6.4）。
# （标签维度的取值由 schemas 层的 Literal 约束，这里不再重复维护一份。）
MAX_TAGS_PER_OBSERVATION = 10


def _now_iso() -> str:
    return to_utc_iso(utc_now())


# ---------- 内部工具 ----------


def _tag_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "dimension": row["dimension"],
        "observation_count": row["observation_count"],
    }


def _observation_tags(conn, observation_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.dimension, 0 AS observation_count
        FROM observation_tags ot
        JOIN tags t ON t.id = ot.tag_id
        WHERE ot.observation_id = ?
        ORDER BY
            t.dimension ASC,
            t.name ASC
        """,
        (observation_id,),
    ).fetchall()
    return [_tag_dict(row) for row in rows]


def _observation_dict(conn, row) -> dict:
    source_text = None
    if row["raw_entry_id"] is not None:
        entry = conn.execute(
            "SELECT content FROM raw_entries WHERE id = ?",
            (row["raw_entry_id"],),
        ).fetchone()
        source_text = entry["content"] if entry else None

    return {
        "id": row["id"],
        "content": row["content"],
        "observed_at": row["observed_at"],
        "observed_at_local": utc_to_local_iso(row["observed_at"], APP_TIMEZONE),
        "local_date": row["local_date"],
        "confidence": row["confidence"],
        "raw_entry_id": row["raw_entry_id"],
        "source_text": source_text,
        "tags": _observation_tags(conn, row["id"]),
        "excluded": row["excluded_at"] is not None,
    }


def _validate_tag_ids(conn, tag_ids: list[int]) -> None:
    for tag_id in tag_ids:
        exists = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if exists is None:
            raise NotFoundError(f"标签不存在：{tag_id}")


def _replace_tags(conn, observation_id: int, tag_ids: list[int]) -> None:
    conn.execute(
        "DELETE FROM observation_tags WHERE observation_id = ?",
        (observation_id,),
    )
    # dict.fromkeys：去重且保持顺序
    for tag_id in dict.fromkeys(tag_ids):
        conn.execute(
            """
            INSERT INTO observation_tags (observation_id, tag_id)
            VALUES (?, ?)
            """,
            (observation_id, tag_id),
        )


def _get_observation_row(conn, observation_id: int):
    row = conn.execute(
        "SELECT * FROM observations WHERE id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("观察不存在。")
    return row


def _validate_local_date(local_date: str) -> None:
    try:
        date.fromisoformat(local_date)
    except ValueError as exc:
        raise DomainValidationError("日期格式无效，应为 YYYY-MM-DD。") from exc


# ---------- L2 观察 ----------


def create_observation(
    data: ObservationCreate,
    source_type: str = "manual",
    message_id: int | None = None,
) -> dict:
    """写一条观察。

    ``source_text`` 必需的：它落进 L1 ``raw_entries``，观察通过 ``raw_entry_id`` 指向它。
    两行在同一个事务里写，所以不存在"有观察没出处"的状态。

    ``message_id`` 是**谁衍生了我**（聊天抽取时传入）。它让"删掉这条消息会影响
    哪些记忆"变得可查 —— 删除前要能告诉你"他还记着这件事"。
    """
    if not data.source_text.strip():
        raise DomainValidationError("观察必须有出处（原始资料不能为空）。")
    if len(data.tag_ids) > MAX_TAGS_PER_OBSERVATION:
        raise DomainValidationError(f"一条观察最多挂 {MAX_TAGS_PER_OBSERVATION} 个标签。")

    now = _now_iso()
    if data.observed_at_local:
        try:
            observed_at = local_datetime_to_utc_iso(data.observed_at_local, APP_TIMEZONE)
        except ValueError as exc:
            raise DomainValidationError("观察时间格式无效。") from exc
    else:
        observed_at = now

    local_date = local_date_of(observed_at, APP_TIMEZONE)

    with get_connection() as conn:
        _validate_tag_ids(conn, data.tag_ids)

        entry = conn.execute(
            """
            INSERT INTO raw_entries (
                source_type, content, occurred_at, created_at, message_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_type, data.source_text.strip(), observed_at, now, message_id),
        )

        cursor = conn.execute(
            """
            INSERT INTO observations (
                content, observed_at, local_date, raw_entry_id, confidence,
                excluded_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                data.content.strip(),
                observed_at,
                local_date,
                entry.lastrowid,
                data.confidence,
                now,
                now,
            ),
        )

        _replace_tags(conn, cursor.lastrowid, data.tag_ids)
        # 写进检索索引（切好词）。**这条不能漏** —— 索引不靠触发器维护
        # （SQL 分不了词），漏一处就是"检索不到"的静默缺口。
        index_memory(conn, "observation", cursor.lastrowid, data.content.strip())

        row = _get_observation_row(conn, cursor.lastrowid)
        return _observation_dict(conn, row)


def delete_raw_entry_if_unreferenced(raw_entry_id: int) -> bool:
    """抹掉一条 L1 出处 —— **只有在没有任何观察引用它时**。

    这是"连档案一起抹掉"的安全阀。只要那条观察还在（比如它是某条判断的
    最后一条依据、删不掉），它的出处就**必须**留着 ——
    否则一条事实会引用一句不存在的话，不变量 5 当场破掉。

    返回是否真的抹掉了。
    """
    with get_connection() as conn:
        referenced = conn.execute(
            "SELECT 1 FROM observations WHERE raw_entry_id = ? LIMIT 1",
            (raw_entry_id,),
        ).fetchone()
        if referenced:
            return False

        cursor = conn.execute("DELETE FROM raw_entries WHERE id = ?", (raw_entry_id,))
        return cursor.rowcount > 0


def observations_from_message(message_id: int) -> list[dict]:
    """某条聊天消息**衍生出了哪些事实**（L2）。

    路径：``observations.raw_entry_id → raw_entries.message_id``。

    删一条聊天消息之前用它算"影响范围"。设计决定是：
    **删聊天记录 ≠ 让他忘掉** —— 所以默认保留，但必须把这件事说出来。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT o.* FROM observations o
            JOIN raw_entries r ON r.id = o.raw_entry_id
            WHERE r.message_id = ?
            ORDER BY o.id ASC
            """,
            (message_id,),
        ).fetchall()
        return [_observation_dict(conn, row) for row in rows]


#: 一次检索最多用多少个"相邻两字"短语去拼 MATCH 串（长消息会切出很多）
MAX_FTS_PHRASES = 60


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # 基本汉字
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
    )


def _segment(text: str) -> str:
    """切成 FTS 的 token：**CJK 一字一个**，拉丁/数字整段保留。

    见 migration 011 的注释：不切的话 unicode61 会把整串中文当成一个词，
    检索完全失效。
    """
    parts: list[str] = []
    buffer: list[str] = []

    for ch in text:
        if _is_cjk(ch):
            if buffer:
                parts.append("".join(buffer))
                buffer = []
            parts.append(ch)
        elif ch.isalnum():
            buffer.append(ch)
        elif buffer:
            parts.append("".join(buffer))
            buffer = []

    if buffer:
        parts.append("".join(buffer))

    return " ".join(parts)


def _fts_query(text: str) -> str | None:
    """把用户的话变成 MATCH 串：**相邻两个 token 的短语**，用 OR 连起来。

    ⚠️ 两头都不能走：

    - 整句当一个短语 = 精确子串匹配。记忆是**短句**、查询是**长句**，
      那样几乎必然落空。
    - 单字用 OR 连起来 —— "的""了"会命中一切，排序彻底失效。

    相邻两字的短语正好卡在中间。少于两个 token 时返回 ``None``，
    交给 `search_memories()` 走 LIKE。

    ⚠️ 用户消息里的标点/引号**已经被 `_segment()` 丢掉了**，
    所以拼进 MATCH 串的都是纯字词，不存在被语法字符搞崩的问题。

    ⚠️ 拉丁/数字词还要**单独**列一遍：它们在查询里常常只出现一次，
    而左右邻居在记忆里通常不一样 —— 只靠相邻对就会漏
    （"DDL 怎么办" 配不上 "集成测试 DDL 还没动"）。
    中文不需要这一步：两字词自己就构成一个相邻对。
    """
    tokens = _segment(text).split()
    if len(tokens) < 2:
        return None

    terms: list[str] = []
    for index in range(len(tokens) - 1):
        phrase = f"{tokens[index]} {tokens[index + 1]}"
        if phrase not in terms:
            terms.append(phrase)

    for token in tokens:
        if not _is_cjk(token[0]) and len(token) >= 2 and token not in terms:
            terms.append(token)

    return " OR ".join(f'"{term}"' for term in terms[:MAX_FTS_PHRASES])


def index_memory(conn, kind: str, row_id: int, content: str) -> None:
    """把一条记忆写进检索索引（切好词）。

    **所有写记忆的地方都要调它** —— 索引不靠触发器维护（SQL 做不了分词），
    所以漏一处就会出现"检索不到"的静默缺口。
    """
    conn.execute(
        "DELETE FROM memory_fts WHERE kind = ? AND row_id = ?",
        (kind, row_id),
    )
    conn.execute(
        "INSERT INTO memory_fts (search_text, kind, row_id) VALUES (?, ?, ?)",
        (_segment(content), kind, row_id),
    )


def unindex_memory(conn, kind: str, row_id: int) -> None:
    """把一条记忆从检索索引里摘掉。"""
    conn.execute(
        "DELETE FROM memory_fts WHERE kind = ? AND row_id = ?",
        (kind, row_id),
    )


def backfill_search_index() -> int:
    """把还没有索引的记忆补进去（幂等），返回补了几条。

    用途：migration 011 建表之前就存在的观察与判断 ——
    分词在 Python 里做，SQL 补不了。启动时调用一次即可。
    """
    indexed = 0
    with get_connection() as conn:
        for kind, table in (
            ("observation", "observations"),
            ("impression", "impressions"),
        ):
            rows = conn.execute(
                f"""
                SELECT t.id, t.content FROM {table} t
                WHERE NOT EXISTS (
                    SELECT 1 FROM memory_fts f
                    WHERE f.kind = ? AND f.row_id = t.id
                )
                """,
                (kind,),
            ).fetchall()
            for row in rows:
                index_memory(conn, kind, row["id"], row["content"])
                indexed += 1
    return indexed


def _rows_by_ids(conn, table: str, ids: list[int]) -> list[dict]:
    """按给定 id 取行，**保持传入的顺序**（那就是相关度顺序）。"""
    if not ids:
        return []

    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    return [
        _observation_dict(conn, by_id[oid])
        if table == "observations"
        else _impression_dict(conn, by_id[oid])
        for oid in ids
        if oid in by_id
    ]


def search_memories(query: str, limit: int = 8) -> dict:
    """按**相关性**检索记忆（FTS5；DEVELOPMENT_PLAN 关键约束）。

    没有它，记忆只能按时间取最近 N 条 —— 三个月前说的那句话永远检索不到。

    返回 ``{"observations": [...], "impressions": [...]}``，各自按相关度排序。
    """
    if not isinstance(query, str):
        # 防御：调用方传了 block 数组之类的东西（真的发生过 —— 带图的消息
        # 被当成检索词）。静默返回空，**绝不 500** ——
        # 这是一个"预览"接口，崩掉会把整页 UI 一起弄哑，而用户看不出原因 ✗。
        # 正确性靠调用方（`chat_service._text_of()`）+ 测试锁住。
        return {"observations": [], "impressions": []}

    clean = query.strip()
    if not clean:
        return {"observations": [], "impressions": []}

    with get_connection() as conn:
        match = _fts_query(clean)
        fetch = max(limit * 2, limit)

        if match is not None:
            rows = conn.execute(
                """
                SELECT kind, row_id FROM memory_fts
                WHERE memory_fts MATCH ?
                ORDER BY bm25(memory_fts)
                LIMIT ?
                """,
                (match, fetch),
            ).fetchall()
            observation_ids = [r["row_id"] for r in rows if r["kind"] == "observation"]
            impression_ids = [r["row_id"] for r in rows if r["kind"] == "impression"]
        else:
            # 只有一两个 token（"累"、"DDL"），拼不出短语 —— 退回 LIKE 扫描。
            # 注意查的是**基表**：索引里存的是切开的文本，LIKE 匹配不到原文。
            like = f"%{clean}%"
            observation_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM observations WHERE content LIKE ? LIMIT ?",
                    (like, fetch),
                ).fetchall()
            ]
            impression_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM impressions WHERE content LIKE ? LIMIT ?",
                    (like, fetch),
                ).fetchall()
            ]

        return {
            "observations": _rows_by_ids(conn, "observations", observation_ids)[:limit],
            "impressions": _rows_by_ids(conn, "impressions", impression_ids)[:limit],
        }


def list_raw_entries(limit: int = 200) -> list[dict]:
    """列出 **L1 原话**（事实的出处 ✓）。

    ⚠️ **只读** ✓ —— L1 只追加、永不改写 ✓（见 `delete_observation()` 的说明 ✓）。
    这一层是"她凭什么这么说"的**最终证据** ✓，所以界面要能翻看 ✓、但**不给编辑** ✓。

    每条带上：

    - `content` / `source_type` / `occurred_at` ✓
    - `message_id` ✓ —— 能在聊天里定位（migration 010 补的列 ✓）
    - `observation_ids` ✓ —— **依赖它的那些事实**（删这条会影响谁 ✓）
    - `impression_ids` ✓ —— 那些事实又被哪些判断当依据用着 ✓

    ⚠️ 按 **id 倒序** ✓ —— 记忆视图里"最新说的"排最上面 ✓。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, source_type, occurred_at, created_at, message_id
            FROM raw_entries
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        if not rows:
            return []

        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        links = conn.execute(
            f"""
            SELECT o.raw_entry_id, o.id AS observation_id, ie.impression_id
            FROM observations o
            LEFT JOIN impression_evidence ie ON ie.observation_id = o.id
            WHERE o.raw_entry_id IN ({placeholders})
            """,
            ids,
        ).fetchall()

    by_entry: dict[int, dict] = {
        row["id"]: {**dict(row), "observation_ids": [], "impression_ids": []}
        for row in rows
    }
    for link in links:
        entry = by_entry.get(link["raw_entry_id"])
        if entry is None:
            continue
        if link["observation_id"] not in entry["observation_ids"]:
            entry["observation_ids"].append(link["observation_id"])
        if (
            link["impression_id"] is not None
            and link["impression_id"] not in entry["impression_ids"]
        ):
            entry["impression_ids"].append(link["impression_id"])

    return list(by_entry.values())


def list_quotes(limit: int = 200) -> list[dict]:
    """**你说过的话 —— 全部** ✓（不只是被抽成事实的那些）。

    ## 为什么单独有这么一条（用户 2026-10-05 提出 ✓）

    用户的原话：「**我要看到的记忆是所有他记下来到文件里的东西**，
    难道只有被归纳出事实的原话才被写进文件吗？」

    → **他猜对了** ✗。`list_raw_entries()` 取的是 **L1 出处** ✓ ——
    而 L1 是**一行一条观察**（`create_observation()` 里同事务写 ✓），
    所以它**只是"成了事实的那些原话"** ✗，是个**子集** ✓。

    **他记下的一切其实分三层** ✓：

    | 表 | 内容 | 完整性 |
    |---|---|---|
    | `messages` | **你发的每一句** | ✅ **全量** ✓ |
    | `raw_entries` | 成了事实的那些原话 | ❌ 子集 ✗ |
    | `observations` / `impressions` | 派生出来的事实与判断 | 子集 ✗ |

    所以这个方法从 `messages` 取 ✓，并附上**它抽出了哪几条事实** ✓
    （走 `raw_entries.message_id` ✓ —— migration 010 补的那一列 ✓；
    `message_id` 为空的原话（手动记的 / 导入的）不会挂到任何消息上 ✓）。

    ⚠️ 只取 `role = 'user'` ✓ —— 这是"**你**说过的话" ✓；
    她的回复在聊天记录里 ✓，不算"他记下的关于你的事" ✓。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, created_at, metadata_json
            FROM messages
            WHERE role = 'user'
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        if not rows:
            return []

        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        facts = conn.execute(
            f"""
            SELECT re.message_id, o.id AS observation_id
            FROM raw_entries re
            JOIN observations o ON o.raw_entry_id = re.id
            WHERE re.message_id IN ({placeholders})
            """,
            ids,
        ).fetchall()

    by_message: dict[int, dict] = {}
    for row in rows:
        metadata = {}
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, ValueError):
                metadata = {}
        by_message[row["id"]] = {
            "id": row["id"],
            "content": row["content"],
            "created_at": row["created_at"],
            "fact_ids": [],
            "imported": bool(metadata.get("imported")),
        }

    for link in facts:
        entry = by_message.get(link["message_id"])
        if entry is not None and link["observation_id"] not in entry["fact_ids"]:
            entry["fact_ids"].append(link["observation_id"])

    return list(by_message.values())


def list_observations(
    local_date: str | None = None,
    tag_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    if local_date is not None:
        _validate_local_date(local_date)

    clauses: list[str] = []
    params: list = []
    if local_date is not None:
        clauses.append("o.local_date = ?")
        params.append(local_date)
    if tag_id is not None:
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM observation_tags ot
                WHERE ot.observation_id = o.id AND ot.tag_id = ?
            )
            """
        )
        params.append(tag_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT o.* FROM observations o
            {where}
            ORDER BY o.observed_at DESC, o.id DESC
            LIMIT ?
            """,
            (*params, max(1, min(limit, 500))),
        ).fetchall()
        return [_observation_dict(conn, row) for row in rows]


def get_observation(observation_id: int) -> dict:
    with get_connection() as conn:
        return _observation_dict(conn, _get_observation_row(conn, observation_id))


def set_observation_tags(observation_id: int, data: ObservationTagsUpdate) -> dict:
    if len(data.tag_ids) > MAX_TAGS_PER_OBSERVATION:
        raise DomainValidationError(f"一条观察最多挂 {MAX_TAGS_PER_OBSERVATION} 个标签。")

    with get_connection() as conn:
        _get_observation_row(conn, observation_id)
        _validate_tag_ids(conn, data.tag_ids)
        _replace_tags(conn, observation_id, data.tag_ids)
        return _observation_dict(conn, _get_observation_row(conn, observation_id))


def delete_observation(observation_id: int) -> None:
    """删掉一条观察——这同时就是「纠错」（这条事实记错了）。

    **不删 L1 原始资料**：L1 只追加、永不被改写，它是事实源。
    印象依据（``impression_evidence``）随观察级联删除。

    但有一条底线：如果这条观察是某条印象的**最后一条依据**，删掉之后那条判断
    就答不出出处了（不变量 5）。这种情况拒绝删除，并告诉你是哪条印象。
    """
    with get_connection() as conn:
        _get_observation_row(conn, observation_id)

        orphaned = conn.execute(
            """
            SELECT i.id AS impression_id
            FROM impression_evidence ie
            JOIN impressions i ON i.id = ie.impression_id
            WHERE ie.observation_id = ?
              AND (
                  SELECT COUNT(*)
                  FROM impression_evidence x
                  WHERE x.impression_id = i.id
              ) <= 1
            """,
            (observation_id,),
        ).fetchall()

        if orphaned:
            ids = "、".join(f"#{row['impression_id']}" for row in orphaned)
            raise DomainValidationError(
                f"这条观察是印象 {ids} 的唯一依据，删掉它那条判断就答不出出处了"
                f"（不变量 5）。请先给那条印象补别的依据。"
            )

        unindex_memory(conn, "observation", observation_id)
        conn.execute("DELETE FROM observations WHERE id = ?", (observation_id,))


# ---------- 标签 ----------


def list_tags() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                t.id,
                t.name,
                t.dimension,
                (
                    SELECT COUNT(*)
                    FROM observation_tags ot
                    WHERE ot.tag_id = t.id
                ) AS observation_count
            FROM tags t
            ORDER BY
                t.dimension ASC,
                t.name ASC
            """
        ).fetchall()
    return [_tag_dict(row) for row in rows]


def get_tag(tag_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                t.id,
                t.name,
                t.dimension,
                (
                    SELECT COUNT(*)
                    FROM observation_tags ot
                    WHERE ot.tag_id = t.id
                ) AS observation_count
            FROM tags t
            WHERE t.id = ?
            """,
            (tag_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("标签不存在。")
    return _tag_dict(row)


def create_tag(data: TagCreate) -> dict:
    name = data.name.strip()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM tags WHERE name = ?",
            (name,),
        ).fetchone()
        if existing is not None:
            raise ConflictError(f"标签已存在：{name}")

        cursor = conn.execute(
            """
            INSERT INTO tags (name, dimension, created_at)
            VALUES (?, ?, ?)
            """,
            (name, data.dimension, _now_iso()),
        )
        tag_id = cursor.lastrowid

    return get_tag(tag_id)


def update_tag(tag_id: int, data: TagUpdate) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if row is None:
            raise NotFoundError("标签不存在。")

        name = data.name.strip() if data.name is not None else row["name"]
        dimension = data.dimension if data.dimension is not None else row["dimension"]

        if name != row["name"]:
            clash = conn.execute(
                "SELECT id FROM tags WHERE name = ? AND id <> ?",
                (name, tag_id),
            ).fetchone()
            if clash is not None:
                raise ConflictError(f"标签名已被占用：{name}")

        conn.execute(
            "UPDATE tags SET name = ?, dimension = ? WHERE id = ?",
            (name, dimension, tag_id),
        )

    return get_tag(tag_id)


def delete_tag(tag_id: int) -> None:
    """删标签**只取消分类，不删记忆**（MEMORY_DESIGN §6.4-B）。

    ``observation_tags`` 会级联清掉关联行，但 ``observations`` 一条都不动——
    那些观察变成"未分类"，她仍然能引用它们。
    """
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if row is None:
            raise NotFoundError("标签不存在。")
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


# ---------- L3 印象 ----------

# 同类反例的触发线（MEMORY_DESIGN §4.4）：
#   第 1 次 → 只标记；第 2 次 → 下调置信度；第 3 次 → 该修订了
REVISION_THRESHOLD = 3
COUNTEREXAMPLE_CONFIDENCE_STEP = 0.2

# 态度（§4.3-C）。注意：**没有"删除印象"这个动作**——事实可以删，判断只能标异议。
IMPRESSION_ATTITUDES = ("none", "agreed", "disputed")


def _validate_observation_ids(conn, observation_ids: list[int]) -> None:
    for observation_id in observation_ids:
        row = conn.execute(
            "SELECT id FROM observations WHERE id = ?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"观察不存在：{observation_id}")


def _replace_evidence(conn, impression_id: int, observation_ids: list[int]) -> None:
    conn.execute(
        "DELETE FROM impression_evidence WHERE impression_id = ?",
        (impression_id,),
    )
    now = _now_iso()
    for observation_id in dict.fromkeys(observation_ids):
        conn.execute(
            """
            INSERT INTO impression_evidence (
                impression_id, observation_id, created_at
            ) VALUES (?, ?, ?)
            """,
            (impression_id, observation_id, now),
        )


def _impression_evidence(conn, impression_id: int) -> list[dict]:
    """印象的依据事件，**带上方向**（R6）：``support`` 支持它 / ``counter`` 反对它。

    修订时必须能区分这两者：反例要被**吸收进**新判断，不能和支撑证据混成一堆。
    """
    rows = conn.execute(
        """
        SELECT o.*, ie.relation AS relation FROM impression_evidence ie
        JOIN observations o ON o.id = ie.observation_id
        WHERE ie.impression_id = ?
        ORDER BY o.observed_at ASC, o.id ASC
        """,
        (impression_id,),
    ).fetchall()

    result = []
    for row in rows:
        item = _observation_dict(conn, row)
        item["relation"] = row["relation"]
        result.append(item)
    return result


def _impression_dict(conn, row) -> dict:
    counterexamples = row["counterexamples"]
    return {
        "id": row["id"],
        "content": row["content"],
        "generation": row["generation"],
        "confidence": row["confidence"],
        "status": row["status"],
        "attitude": row["attitude"],
        "counterexamples": counterexamples,
        "superseded_by": row["superseded_by"],
        # 攒够反例就该修订了。做成**计算属性**而不是存字段，避免状态漂移。
        "needs_revision": (
            counterexamples >= REVISION_THRESHOLD and row["status"] != "superseded"
        ),
        "evidence": _impression_evidence(conn, row["id"]),
        "excluded": row["excluded_at"] is not None,
    }


def _get_impression_row(conn, impression_id: int):
    row = conn.execute(
        "SELECT * FROM impressions WHERE id = ?",
        (impression_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("印象不存在。")
    return row


def used_evidence_ids() -> set[int]:
    """已经被某条印象当作依据的观察 id。

    归纳流程用它避免"同一批事件被反复归纳成多条印象"。
    放在这里而不是让 workflow 直接查表 —— workflows 层不持有数据库细节。
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT observation_id FROM impression_evidence"
        ).fetchall()
    return {row["observation_id"] for row in rows}


def create_impression(
    content: str,
    evidence_ids: list[int],
    confidence: float = 0.5,
) -> dict:
    """建一条印象（L3）。

    ``evidence_ids`` 是**必需**的：不变量 5 —— 答不出依据的判断不许说出口。
    LLM 归纳（S4 的下一个子步）会调用这个函数；当前也可以手工/测试创建。
    """
    if not content.strip():
        raise DomainValidationError("印象内容不能为空。")
    if not evidence_ids:
        raise DomainValidationError(
            "印象必须有依据事件（不变量 5：没有出处不许说'我记得'）。"
        )

    now = _now_iso()
    with get_connection() as conn:
        _validate_observation_ids(conn, evidence_ids)

        cursor = conn.execute(
            """
            INSERT INTO impressions (
                content, generation, confidence, status, counterexamples,
                superseded_by, excluded_at, attitude, created_at, updated_at
            ) VALUES (?, 1, ?, 'active', 0, NULL, NULL, 'none', ?, ?)
            """,
            (content.strip(), confidence, now, now),
        )
        impression_id = cursor.lastrowid
        _replace_evidence(conn, impression_id, evidence_ids)
        index_memory(conn, "impression", impression_id, content.strip())

        return _impression_dict(conn, _get_impression_row(conn, impression_id))


def list_impressions(include_superseded: bool = True) -> list[dict]:
    """印象列表。

    默认**包含历史代际**——"旧版本永久保留"是设计的一部分，
    她要说得出"我两个月前不是这么看你的"。
    """
    query = "SELECT * FROM impressions"
    if not include_superseded:
        query += " WHERE status <> 'superseded'"
    query += " ORDER BY id DESC"

    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
        return [_impression_dict(conn, row) for row in rows]


def get_impression(impression_id: int) -> dict:
    with get_connection() as conn:
        return _impression_dict(conn, _get_impression_row(conn, impression_id))


def set_impression_attitude(impression_id: int, attitude: str) -> dict:
    """标记你对这条印象的态度（MEMORY_DESIGN §4.3-C）。

    - ``agreed``   认同 —— 她引用时理直气壮
    - ``disputed`` 异议 —— 印象**不消失**，但她引用时必须带上"你不同意"
    - ``none``     撤回表态

    **没有"删除印象"这个动作**：事实可以删（删那条观察），判断只能标异议。
    """
    if attitude not in IMPRESSION_ATTITUDES:
        raise DomainValidationError(f"不支持的态度：{attitude}")

    with get_connection() as conn:
        row = _get_impression_row(conn, impression_id)
        if row["status"] == "superseded":
            raise DomainValidationError("这一代印象已被取代，不能再标态度。")

        status = "disputed" if attitude == "disputed" else "active"
        conn.execute(
            """
            UPDATE impressions
            SET attitude = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (attitude, status, _now_iso(), impression_id),
        )
        return _impression_dict(conn, _get_impression_row(conn, impression_id))


def add_supporting_evidence(impression_id: int, observation_ids: list[int]) -> dict:
    """给**已有**印象补同方向证据（设计裁决 R6）。

    ⚠️ 这里是 R6 那条关键修正的落点：

    > 有印象 + 同方向 → 只记 support 证据，**不创建新一代**

    为什么不创建新一代：
    1. 同方向的新事件只会让同一个结论重复出现 —— 那是噪声，不是记忆
    2. 会污染 §7.3 的演化史（"她怎么改了看法"退化成同一结论的重复列表）
    3. "迭代"的含义是**变化**，不是重复累积
    """
    if not observation_ids:
        raise DomainValidationError("补证据至少要有一条。")

    with get_connection() as conn:
        row = _get_impression_row(conn, impression_id)
        if row["status"] == "superseded":
            raise DomainValidationError("这一代印象已被取代，不能再补证据。")

        _validate_observation_ids(conn, observation_ids)
        now = _now_iso()

        for observation_id in dict.fromkeys(observation_ids):
            # 方向**可被改写**：同一条事件这次是 support，
            # 下次方向判成 counter 时必须能翻过来（否则反例会被静默吞掉）。
            conn.execute(
                """
                INSERT INTO impression_evidence (
                    impression_id, observation_id, relation, created_at
                ) VALUES (?, ?, 'support', ?)
                ON CONFLICT(impression_id, observation_id)
                DO UPDATE SET relation = 'support'
                """,
                (impression_id, observation_id, now),
            )

        conn.execute(
            "UPDATE impressions SET updated_at = ? WHERE id = ?",
            (now, impression_id),
        )
        return _impression_dict(conn, _get_impression_row(conn, impression_id))


def impressions_for_tag(tag_id: int) -> list[dict]:
    """挂了这个标签的事实，已经被归纳进哪些印象。

    印象本身不挂标签（标签是**观察**的属性），所以只能反查：
    ``impression → impression_evidence → observation_tags → tag``。

    归纳流程靠它回答那个 R6 的核心问题：
    "这个标签是**第一次**形成印象，还是**已有**印象要补证据？"
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT i.*
            FROM impressions i
            JOIN impression_evidence ie ON ie.impression_id = i.id
            JOIN observation_tags ot ON ot.observation_id = ie.observation_id
            WHERE ot.tag_id = ?
              AND i.status != 'superseded'
            ORDER BY i.generation DESC, i.id DESC
            """,
            (tag_id,),
        ).fetchall()
        return [_impression_dict(conn, row) for row in rows]


def record_counterexample(
    impression_id: int,
    observation_id: int | None = None,
) -> dict:
    """记一条**同类反例**（MEMORY_DESIGN §4.4）。

    1 次 → 只标记；2 次 → 下调置信度；3 次 → ``needs_revision`` 亮起。

    这里**不自动改写印象内容**——写新一代需要 LLM 归纳（S4 的下一个子步）。
    本函数只负责计数、降置信、把"该修订了"标出来。
    """
    with get_connection() as conn:
        row = _get_impression_row(conn, impression_id)
        if row["status"] == "superseded":
            raise DomainValidationError("这一代印象已被取代，不能再加反例。")

        if observation_id is not None:
            _validate_observation_ids(conn, [observation_id])

        count = row["counterexamples"] + 1
        confidence = row["confidence"]
        if count >= 2:
            # 第 2 次反例起下调（方案里的例子：0.7 → 0.5）
            confidence = max(0.0, confidence - COUNTEREXAMPLE_CONFIDENCE_STEP)

        now = _now_iso()
        conn.execute(
            """
            UPDATE impressions
            SET counterexamples = ?, confidence = ?, updated_at = ?
            WHERE id = ?
            """,
            (count, confidence, now, impression_id),
        )

        if observation_id is not None:
            # 反例本身也是证据，而且必须**标成 counter** ——
            # 否则修订时看不出哪条是反对它的（设计裁决 R6）。
            # 方向可被改写：同一条事件先被判 support、后来改判 counter 时要能翻过来。
            conn.execute(
                """
                INSERT INTO impression_evidence (
                    impression_id, observation_id, relation, created_at
                ) VALUES (?, ?, 'counter', ?)
                ON CONFLICT(impression_id, observation_id)
                DO UPDATE SET relation = 'counter'
                """,
                (impression_id, observation_id, now),
            )

        return _impression_dict(conn, _get_impression_row(conn, impression_id))


def revise_impression(
    impression_id: int,
    new_content: str,
    evidence_ids: list[int] | None = None,
    confidence: float | None = None,
) -> dict:
    """生成**新一代**印象，旧版本永久保留（MEMORY_DESIGN §4.3）。

    写新内容需要 LLM 归纳（S4 的下一个子步）；本函数只负责代际关系：

    - 旧代 → ``status='superseded'`` + ``superseded_by``
    - 新代 ``generation + 1``、``counterexamples`` 归零
    - 不传 ``evidence_ids`` 就继承旧代的全部依据
    """
    if not new_content.strip():
        raise DomainValidationError("新的印象内容不能为空。")

    now = _now_iso()
    with get_connection() as conn:
        old = _get_impression_row(conn, impression_id)
        if old["status"] == "superseded":
            raise DomainValidationError("这一代已被取代，不能再修订。")

        if evidence_ids is None:
            evidence_ids = [
                row["observation_id"]
                for row in conn.execute(
                    """
                    SELECT observation_id FROM impression_evidence
                    WHERE impression_id = ?
                    """,
                    (impression_id,),
                ).fetchall()
            ]
        else:
            _validate_observation_ids(conn, evidence_ids)

        if not evidence_ids:
            raise DomainValidationError("新的一代也必须有依据事件（不变量 5）。")

        cursor = conn.execute(
            """
            INSERT INTO impressions (
                content, generation, confidence, status, counterexamples,
                superseded_by, excluded_at, attitude, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', 0, NULL, NULL, 'none', ?, ?)
            """,
            (
                new_content.strip(),
                old["generation"] + 1,
                old["confidence"] if confidence is None else confidence,
                now,
                now,
            ),
        )
        new_id = cursor.lastrowid
        _replace_evidence(conn, new_id, evidence_ids)
        index_memory(conn, "impression", new_id, new_content.strip())

        conn.execute(
            """
            UPDATE impressions
            SET status = 'superseded', superseded_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_id, now, impression_id),
        )

        return _impression_dict(conn, _get_impression_row(conn, new_id))


def observations_supporting(
    tag_id: int,
    min_count: int = REVISION_THRESHOLD,
) -> dict:
    """「同类事件累积 3 次」的触发线（MEMORY_DESIGN §4.4）。

    返回挂了这个标签的事件；够 ``min_count`` 条就说明**够格归纳成一条印象**了。

    这里**不生成印象文本**——那是 LLM 归纳的事（S4 的下一个子步）。
    本函数只回答"素材够不够"。
    """
    observations = list_observations(tag_id=tag_id, limit=500)
    return {
        "tag_id": tag_id,
        "count": len(observations),
        "min_count": min_count,
        "ready": len(observations) >= min_count,
        "observations": observations,
    }


# ---------- 演化史（S5「她眼中的我」） ----------


def _lineage_dict(conn, chain: list) -> dict:
    current = chain[-1]
    return {
        "current_id": current["id"],
        "generation": current["generation"],
        "current": _impression_dict(conn, current),
        # 从旧到新；最后一条就是"现在的看法"
        "history": [
            {**_impression_dict(conn, row), "is_current": row["id"] == current["id"]}
            for row in chain
        ],
    }


def list_impression_lineages() -> list[dict]:
    """把印象按**代际链**聚合（S5）。

    一条链 = 一条判断的演化史：旧代 → 新代 → 最新代。
    旧版本永久保留（MEMORY_DESIGN §4.3），所以"她是怎么改看法的"是可查的。

    链头的判定：**没有任何印象的 ``superseded_by`` 指向它**。
    这样"版本列表"就被组装成了"一条判断的来龙去脉"，界面才有东西可以叙事。
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM impressions ORDER BY id ASC").fetchall()
        by_id = {row["id"]: row for row in rows}
        pointed_to = {
            row["superseded_by"] for row in rows if row["superseded_by"] is not None
        }

        lineages = []
        for row in rows:
            if row["id"] in pointed_to:
                continue  # 有前代指向它 → 不是链头

            chain = []
            cursor = row
            seen: set[int] = set()
            # seen 防的是脏数据造成的环，正常数据永远走不到
            while cursor is not None and cursor["id"] not in seen:
                seen.add(cursor["id"])
                chain.append(cursor)
                next_id = cursor["superseded_by"]
                cursor = by_id.get(next_id) if next_id is not None else None

            lineages.append(_lineage_dict(conn, chain))

        lineages.sort(key=lambda item: item["current_id"], reverse=True)
        return lineages
