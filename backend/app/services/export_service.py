"""导出 / 备份（用户要求）。

`TODO.md` 里那条债的原话：「记忆攒了几个月之后，你只能手动拷 `storage/app.db`」✗。
整套东西的价值就是"**它记得你**"，而它只有一份 —— 所以这是最实在的一处债。

两条路，用途不同：

- **JSON 导出** —— 可读、可存档、可 grep。给人看的，也是"哪天要搬走"的形态。
- **ZIP 备份** —— 数据库 + 图片。这是**可以直接换回去**的那一份 ✓。

⚠️ 两个都不含 `.env`（里面有 API Key ✗）—— 密钥你自己留着，恢复时重填 ✓。
"""

import base64
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.database.connection import get_connection
from app.services import image_service

#: 导出时**排除**的表：纯派生，能重建。
DERIVED_TABLES = ("memory_fts", "sqlite_sequence")

#: 单表导出行数上限。防止某张表异常膨胀时把内存吃光；
#: 正常的单用户用量远低于它（一天几十条消息）。
MAX_ROWS_PER_TABLE = 200_000

BACKUP_DIR = Path(image_service.IMAGES_DIR).parent / "backups"

_README = """这是 life-ai 的备份。

  app.db     —— 你全部的记忆与生活数据（SQLite）
  images/    —— 你发过的图片

怎么恢复：
  1. 停掉后端
  2. 用这里的 app.db 覆盖 backend/storage/app.db
  3. 把 images/ 放回 backend/storage/images/
  4. 重启后端

⚠️ 这个包里**没有** .env —— API Key 请重新填一次（设置页里填）。
"""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _cell(value):
    """SQLite 可能给出 ``bytes``（BLOB 列）—— JSON 塞不进去，转 base64 ✓。

    没有这一步，某天有一列存了二进制，导出会在**运行到那一行时**才炸 ✗，
    而且报错完全指不出是哪张表 ✓。
    """
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def export_payload() -> dict:
    """把所有表导成一个 dict。**只读**，不改任何数据。"""
    conn = get_connection()
    try:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            if row["name"] not in DERIVED_TABLES
        ]
        data: dict[str, list[dict]] = {}
        for table in tables:
            rows = conn.execute(
                f'SELECT * FROM "{table}" LIMIT ?', (MAX_ROWS_PER_TABLE,)
            ).fetchall()
            data[table] = [
                {key: _cell(value) for key, value in dict(row).items()}
                for row in rows
            ]
    finally:
        conn.close()

    return {
        "format": "life-ai-export",
        "format_version": 1,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "你全部的记忆与生活数据。⚠️ 图片**文件**不在这个 JSON 里 —— "
            "它们在 backend/storage/images/，要完整备份请用 ZIP 那个按钮。"
        ),
        "tables": data,
    }


def write_backup_zip(dest: Path | None = None) -> Path:
    """把**数据库 + 图片**打成一个 zip，返回它的路径。

    数据库用 SQLite 的 ``backup()`` 拷一份**一致的**快照 ——
    WAL 模式下直接拷文件可能拷到不一致的状态 ✗。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target_zip = dest or (BACKUP_DIR / f"backup-{_stamp()}.zip")

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "app.db"
        source = get_connection()
        try:
            copy = sqlite3.connect(snapshot)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()

        with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, "app.db")
            images = Path(image_service.IMAGES_DIR)
            if images.exists():
                for path in sorted(images.iterdir()):
                    if path.is_file():
                        bundle.write(path, f"images/{path.name}")
            bundle.writestr("README.txt", _README)

    return target_zip
