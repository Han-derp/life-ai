"""人格文件解析（DEVELOPMENT_PLAN v2.0 · S3）。

**人格只有一份**，放在 ``backend/personas/<name>/``：

```text
backend/personas/<name>/
├── persona.md   ← 必需：它是谁
└── meta.json    ← 可选：{"display_name": "行动搭档"}，只影响界面显示
```

本模块只管**文件**——"当前启用哪一个"由 ``services/persona_service`` 决定
（那里才访问数据库），这样 core 不会反向依赖 database。
"""

import json
import shutil
from functools import lru_cache
from pathlib import Path

from app.core.exceptions import DomainValidationError, NotFoundError

PERSONAS_DIR = Path(__file__).resolve().parents[2] / "personas"
PERSONA_FILENAME = "persona.md"
PERSONA_META_FILENAME = "meta.json"
MAX_NAME_LENGTH = 64

# 目录名白名单：避免路径穿越（"." / "/" / "\" 都不在白名单里）。
_ALLOWED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def is_valid_persona_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= MAX_NAME_LENGTH
        and all(ch in _ALLOWED_CHARS for ch in name)
    )


def persona_dir(name: str) -> Path:
    if not is_valid_persona_name(name):
        raise DomainValidationError(
            "人格名不合法：只允许字母、数字、下划线和连字符，最长 64 个字符。"
        )
    return PERSONAS_DIR / name


def persona_path(name: str) -> Path:
    return persona_dir(name) / PERSONA_FILENAME


def persona_meta_path(name: str) -> Path:
    return persona_dir(name) / PERSONA_META_FILENAME


def list_personas() -> list[str]:
    """列出可用人格（含有 persona.md 的目录），按名字排序。meta.json 可选。"""
    if not PERSONAS_DIR.exists():
        return []
    return sorted(
        item.name
        for item in PERSONAS_DIR.iterdir()
        if item.is_dir() and (item / PERSONA_FILENAME).exists()
    )


def persona_exists(name: str) -> bool:
    try:
        return persona_path(name).exists()
    except DomainValidationError:
        return False


@lru_cache(maxsize=32)
def _read_text_cached(path_str: str, mtime_ns: int) -> str:
    # mtime 参与缓存键：用户直接编辑文件之后，无需重启即可生效。
    return Path(path_str).read_text(encoding="utf-8").strip()


@lru_cache(maxsize=32)
def _read_json_cached(path_str: str, mtime_ns: int) -> str:
    return Path(path_str).read_text(encoding="utf-8")


def _clear_caches() -> None:
    _read_text_cached.cache_clear()
    _read_json_cached.cache_clear()


def load_persona_text(name: str) -> str:
    path = persona_path(name)
    if not path.exists():
        raise NotFoundError(f"找不到人格文件：{path}")
    return _read_text_cached(str(path), path.stat().st_mtime_ns)


def load_persona_meta(name: str) -> dict:
    """人格元信息。缺失或损坏时返回 ``{}``——界面可以退化成目录名，不该报错。"""
    path = persona_meta_path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(_read_json_cached(str(path), path.stat().st_mtime_ns))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def persona_display_name(name: str) -> str:
    """给界面用的名字：meta.json 里有就用它，否则退回目录名。"""
    value = str(load_persona_meta(name).get("display_name") or "").strip()
    return value or name


def save_persona_text(name: str, content: str) -> Path:
    """写入人格文件（目录不存在则创建）。不在这里判断"是否已存在"。

    ⚠️ 覆盖之前**先留一份 `persona.md.bak`**。

    原设计因此不许应用内编辑（`api/v1/persona.py` 里写着"避免应用内误覆盖"）——
    但那条约束的**目的**是"别让一次手滑毁掉人格"，不是"必须用记事本" ✗。
    用备份满足那个目的之后，界面上的编辑器就不会变成风险 ✓。
    """
    path = persona_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        shutil.copyfile(path, path.with_suffix(".md.bak"))

    path.write_text(content.strip() + "\n", encoding="utf-8")
    _clear_caches()
    return path


def save_persona_meta(name: str, meta: dict) -> Path:
    path = persona_meta_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _clear_caches()
    return path
