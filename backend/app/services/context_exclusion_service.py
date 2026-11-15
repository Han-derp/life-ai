"""上下文块排除状态（DEVELOPMENT_PLAN v2.0 · S2）。

三种开关状态（见 CONTEXT_CONTROL_PLAN §6）：

- ``include``         正常发送（默认）
- ``exclude_once``    只这次不发、下次恢复 —— 进程内一次性，由下一次真实请求消费
- ``exclude_always``  永久不发 —— 落库到 ``context_exclusions``，且**可逆**

"永久"必须可逆：面板里被排除的块保留显示（灰掉），不消失。
"我关过什么"本身是需要可查的。
"""

from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection

# exclude_once 只在本进程内有效。单用户 local-first 应用下，
# 它等价于"下一个请求生效一次"。不落库，符合方案"只存当次请求"的约定。
_once_ids: set[str] = set()


# ---------- exclude_always（持久） ----------


def list_excluded_ids() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT block_id FROM context_exclusions").fetchall()
    return {row["block_id"] for row in rows}


def set_excluded(block_id: str, excluded: bool) -> None:
    """写入 / 解除永久排除。解除时不删任何内容，只是取消排除。"""
    with get_connection() as conn:
        if excluded:
            conn.execute(
                """
                INSERT INTO context_exclusions (block_id, excluded_at)
                VALUES (?, ?)
                ON CONFLICT(block_id) DO NOTHING
                """,
                (block_id, to_utc_iso(utc_now())),
            )
        else:
            conn.execute(
                "DELETE FROM context_exclusions WHERE block_id = ?",
                (block_id,),
            )


# ---------- exclude_once（进程内一次性） ----------


def list_once_ids() -> set[str]:
    return set(_once_ids)


def set_once(block_id: str, excluded: bool) -> None:
    if excluded:
        _once_ids.add(block_id)
    else:
        _once_ids.discard(block_id)


def consume_once_ids() -> set[str]:
    """取出并清空一次性排除。由 chat 请求在组装完 prompt 后调用。"""
    global _once_ids
    taken = set(_once_ids)
    _once_ids = set()
    return taken


def clear_once_ids() -> None:
    """仅供测试/重置使用。"""
    global _once_ids
    _once_ids = set()
