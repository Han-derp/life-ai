"""导出 / 备份。

`TODO.md` 里那条债原话：「记忆攒了几个月之后，你只能手动拷 `storage/app.db`」✗。
整套东西的价值就是"它记得你"，而它只有一份 —— 所以这是最实在的一处债。
"""

import json
import zipfile

import pytest

from app.schemas.memory import ObservationCreate
from app.services import export_service, memory_service


@pytest.fixture(autouse=True)
def isolated_backups(monkeypatch, tmp_path):
    """**别把测试备份写进用户真实的 `backend/storage/backups/`** ✗。

    没有这一条，跑一次测试就在他的项目里多一个 zip ✓。
    """
    monkeypatch.setattr(export_service, "BACKUP_DIR", tmp_path / "backups")


# ---------- JSON 导出 ----------


def test_the_export_has_the_expected_tables(client):
    payload = export_service.export_payload()

    assert payload["format"] == "life-ai-export"
    assert payload["format_version"] == 1
    assert "messages" in payload["tables"]
    assert "observations" in payload["tables"]
    assert "impressions" in payload["tables"]


def test_derived_tables_are_left_out(client):
    """`memory_fts` 是纯派生表，能从别处重建 —— 导出里不该有它。"""
    assert "memory_fts" not in export_service.export_payload()["tables"]


def test_the_export_is_json_serialisable(client):
    """★ 导出必须**能写成 JSON**。

    任何一列存了 `bytes`（BLOB），`json.dumps` 就会炸 ✗ ——
    而且是运行到那一行才炸，报错指不出是哪张表 ✓。
    """
    memory_service.create_observation(
        ObservationCreate(content="测试一条", source_text="我测试一条")
    )

    text = json.dumps(export_service.export_payload(), ensure_ascii=False)

    assert "测试一条" in text


def test_bytes_become_base64_not_a_crash():
    assert export_service._cell(b"\x00\x01") == "AAE="
    assert export_service._cell("正常文本") == "正常文本"
    assert export_service._cell(None) is None


# ---------- ZIP 备份 ----------


def test_the_backup_zip_has_the_database(client, tmp_path):
    path = export_service.write_backup_zip(tmp_path / "b.zip")

    with zipfile.ZipFile(path) as bundle:
        assert "app.db" in bundle.namelist()


def test_the_backup_zip_explains_how_to_restore(client, tmp_path):
    """备份**必须自带恢复说明** —— 几个月后再看，没人记得怎么放回去 ✗。"""
    path = export_service.write_backup_zip(tmp_path / "b.zip")

    with zipfile.ZipFile(path) as bundle:
        readme = bundle.read("README.txt").decode("utf-8")

    assert "app.db 覆盖" in readme
    assert "API Key" in readme  # 要说明它**不含**密钥 ✓


def test_the_backup_does_not_leave_anything_behind(client, tmp_path, monkeypatch):
    """打包用的临时快照要清掉。"""
    backups = tmp_path / "backups"
    monkeypatch.setattr(export_service, "BACKUP_DIR", backups)

    export_service.write_backup_zip()

    assert len(list(backups.glob("*.zip"))) == 1


# ---------- 端点 ----------


def test_the_json_endpoint_downloads_a_file(client):
    response = client.get("/api/v1/export/json")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.json()["format"] == "life-ai-export"


def test_the_backup_endpoint_downloads_a_zip(client):
    response = client.get("/api/v1/export/backup.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
