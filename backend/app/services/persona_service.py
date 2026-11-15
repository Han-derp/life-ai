"""当前启用人格（DEVELOPMENT_PLAN v2.0 · S3）。

"人格只有一份"——内容在 ``backend/personas/<name>/persona.md``；
这里决定**当前启用哪一个**：``app_settings['persona']`` 覆盖 ``.env`` 的默认值，
所以应用内切换既不需要重启，也不需要改 .env。
"""

from app.core.config import PERSONA
from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.persona_loader import (
    list_personas,
    load_persona_text,
    persona_display_name,
    persona_exists,
    save_persona_meta,
    save_persona_text,
)
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection

ACTIVE_PERSONA_KEY = "persona"


def active_persona_name() -> str:
    """当前人格名：``app_settings`` 优先，回落到 ``.env`` 的 ``PERSONA``。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (ACTIVE_PERSONA_KEY,),
        ).fetchone()

    name = (row["value"].strip() if row and row["value"] else "") or PERSONA

    # 数据库里可能存着一个已经被删掉的人格目录 —— 退回默认，
    # 不要让整个应用因为一个人格名而起不来。
    if not persona_exists(name):
        return PERSONA if persona_exists(PERSONA) else name
    return name


def set_active_persona(name: str) -> str:
    if not persona_exists(name):
        raise NotFoundError(f"人格不存在：{name}")

    now = to_utc_iso(utc_now())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (ACTIVE_PERSONA_KEY, name, now),
        )
    return name


def _persona_summary(name: str) -> dict:
    return {"name": name, "display_name": persona_display_name(name)}


def get_persona_state() -> dict:
    """当前人格 + 可用列表 + 人格全文（面板要能看到她是谁）。"""
    active = active_persona_name()
    return {
        "active": active,
        "active_display_name": persona_display_name(active),
        "available": [_persona_summary(name) for name in list_personas()],
        "content": load_persona_text(active),
    }


def save_persona_content(content: str) -> dict:
    """改写**当前人格**的正文（界面上的"编辑人格"）。

    和 `create_persona` 的两点不同：

    1. **允许覆盖** —— 用户在界面上点的是"编辑"，那是明确的意思表示 ✓
       （`create_persona` 拒绝覆盖是为了防止**新建时**误踩已有的名字 ✗）。
    2. **只能改当前激活的那个人格** —— 不让界面传名字进来，
       否则多个人格时那就是误操作的入口 ✗。
       写之前 `save_persona_text` 会留 `.bak` ✓，改坏了能捞回来。
    """
    clean = (content or "").strip()
    if len(clean) < 10:
        raise DomainValidationError(
            "人格内容太短了（至少 10 个字）。空人格会让她退回客服腔 ✗ —— "
            "那正是这份文件唯一要防的事。"
        )

    active = active_persona_name()
    save_persona_text(active, clean)
    return get_persona_state()


def create_persona(
    name: str,
    content: str,
    activate: bool = True,
    display_name: str | None = None,
) -> dict:
    """新建一个人格。

    已存在则拒绝——**编辑**请直接改 ``personas/<name>/persona.md``
    （README 就是这么教的，也避免应用内误覆盖现有的人格）。
    """
    if persona_exists(name):
        raise DomainValidationError(f"人格已存在：{name}。要修改请直接编辑该文件。")

    save_persona_text(name, content)
    if display_name and display_name.strip():
        save_persona_meta(name, {"display_name": display_name.strip()})

    if activate:
        set_active_persona(name)
    return get_persona_state()
