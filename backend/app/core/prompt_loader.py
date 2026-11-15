from functools import lru_cache
from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


@lru_cache(maxsize=32)
def _read_prompt_cached(path_str: str, mtime_ns: int) -> str:
    """按**文件修改时间**缓存。

    ⚠️ 原来是对**文件名**直接 `lru_cache` ✗ —— 后果是改了提示词**必须重启后端**
    才生效。而提示词是这套东西里影响最大的一处（改 25 行的口吻就能让她的语气变样），
    每次都要重启实在没必要。把 mtime 放进 key 之后，改文件**立刻生效** ✓。

    （和 `persona_loader` 同一个做法 —— 那边一直是 mtime 键的，
    所以人格文件改完不用重启，只有提示词要，这个不对称本身就是 bug ✗。）
    """
    return Path(path_str).read_text(encoding="utf-8").strip()


def load_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"找不到 Prompt 文件：{path}")
    return _read_prompt_cached(str(path), path.stat().st_mtime_ns)
