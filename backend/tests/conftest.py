import gc
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every test at a fresh temp SQLite file, then bootstrap it.

    This must run before any `app.*` module is imported, so APP_DB_PATH is
    patched via env var + monkeypatching app.database.connection.DB_PATH,
    which is what every service module imports at call time.
    """
    tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmp_dir.name) / "test.db"
    monkeypatch.setenv("APP_DB_PATH", str(db_path))

    from app.database import connection

    monkeypatch.setattr(connection, "DB_PATH", db_path)

    from app.database.bootstrap import bootstrap_database

    bootstrap_database()

    yield db_path

    # SQLite (WAL mode) keeps -wal/-shm file handles open on Windows until
    # the connection and any lingering references are garbage collected.
    # Force a collection pass so TemporaryDirectory cleanup doesn't race
    # against the OS still holding a lock on test.db.
    gc.collect()
    tmp_dir.cleanup()


@pytest.fixture(autouse=True)
def isolated_llm_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """把测试模型配置钉在安全默认值上，绝不读取开发者本机的真实路由。"""
    from app.core import config
    from app.llm import factory

    for task, name in factory.DEFAULT_PROVIDER_BY_TASK.items():
        key = factory._ENV_KEY_BY_TASK[task]
        if not hasattr(config, key):
            raise AssertionError(
                f"factory 给任务 {task!r} 配了环境变量常量 {key}，但 config.py 里没有它。"
            )
        monkeypatch.setattr(config, key, name)

    # 测试不能继承开发者 .env 中的访问密码或旧版 Ollama 配置。
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    for key in (
        "LLM_LOCAL_ONLY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "OLLAMA_NUM_CTX",
        "OLLAMA_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    for tier in config.TIERS:
        prefix = f"LLM_TIER_{tier.upper()}_"
        monkeypatch.setenv(prefix + "PROVIDER", config.DEFAULT_TIER_PROVIDER[tier])
        monkeypatch.setenv(prefix + "BASE_URL", "")
        monkeypatch.setenv(prefix + "API_KEY", "")
        monkeypatch.setenv(prefix + "MODEL", "")

    factory.reset_cache()
    yield
    factory.reset_cache()


@pytest.fixture
def client(isolated_database: Path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
