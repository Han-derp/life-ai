"""聊天图片的本地存储。

**为什么存文件、不塞进数据库**：``messages.content`` 是 TEXT，
base64 塞进去会让每条带图的消息变成几百 KB —— 而组装上下文时
**每次请求都要读它** ✗。

存法：``storage/images/<uuid>.<ext>``，把引用写进 ``messages.metadata_json``。
所以**不需要 migration** —— `metadata_json` 本来就在那儿 ✓。

⚠️ 图片是**要发给云端的**（那是它唯一能被"看见"的方式）。
所以前端必须先把这件事说清楚 —— 这是用户定下的原则：
"本该只在本机的东西离开本机时，要告诉我一声"。
"""

import base64
import binascii
import uuid
from pathlib import Path

from app.core.config import BACKEND_DIR
from app.core.exceptions import DomainValidationError

#: 允许的类型 → 落盘扩展名
ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

#: 单张上限。前端会先把长边缩到 1024px，正常只有一两百 KB。
MAX_IMAGE_BYTES = 5 * 1024 * 1024

IMAGES_DIR = BACKEND_DIR / "storage" / "images"


def save_data_url(data_url: str) -> dict:
    """把 ``data:image/jpeg;base64,...`` 落成文件。

    返回可以直接写进 ``metadata_json`` 的字典：
    ``{"path": "<文件名>", "mime": ..., "bytes": ...}``
    """
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise DomainValidationError("图片格式不对（需要 data URL）。")

    header, _, payload = data_url.partition(",")
    if not payload:
        raise DomainValidationError("图片内容为空。")

    mime = header[5:].split(";")[0].strip().lower()
    extension = ALLOWED_MIME.get(mime)
    if extension is None:
        raise DomainValidationError(
            f"不支持的图片类型：{mime or '未知'}（支持 jpg / png / webp / gif）。"
        )

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DomainValidationError("图片解码失败。") from exc

    if not raw:
        raise DomainValidationError("图片内容为空。")
    if len(raw) > MAX_IMAGE_BYTES:
        raise DomainValidationError(
            f"图片太大了（{len(raw) // 1024} KB），上限 "
            f"{MAX_IMAGE_BYTES // 1024 // 1024} MB。"
        )

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"
    (IMAGES_DIR / name).write_bytes(raw)

    return {"path": name, "mime": mime, "bytes": len(raw)}


def as_data_url(image: dict) -> str:
    """读回来变成 data URL（发给模型用）。

    文件不在了就返回空串 —— 调用方据此降级成纯文本，
    而不是让整次请求崩掉。
    """
    path = IMAGES_DIR / str(image.get("path", ""))
    if not path.is_file():
        return ""

    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{image.get('mime', 'image/jpeg')};base64,{payload}"


def delete_saved(image: dict | None) -> bool:
    """删掉一张已落盘的图片。删掉了返回 ``True``。

    ## 为什么必须做

    删消息原来只删数据库行 ✓，**文件会一直留着** ✗ ——
    攒久了就是一堆谁也认不出的孤儿，而且里面全是你发过的图 ✓。

    ## 安全

    ⚠️ **只按文件名删，并且只在 `IMAGES_DIR` 里删。**
    `metadata_json` 里那个 `path` 是我们自己写的 ✓，但它终究是**可被编辑的数据** ✗ ——
    万一哪天有人往里塞了 `../../`，这里也不能照着删 ✓。
    """
    if not isinstance(image, dict):
        return False

    name = str(image.get("path") or "").strip()
    if not name:
        return False

    # 只取文件名那一段：`Path("../../etc/passwd").name` 就是 `"passwd"` ✓
    target = IMAGES_DIR / Path(name).name
    if target.parent != IMAGES_DIR or not target.is_file():
        return False

    try:
        target.unlink()
    except OSError:
        # 删不掉**不该让"删消息"失败** ✓ —— 大不了留个孤儿文件
        return False
    return True


def delete_many(images: list[dict]) -> int:
    """批量删，返回成功几个。"""
    return sum(1 for image in images if delete_saved(image))
