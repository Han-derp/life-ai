"""S3 人格 API。"""

from app.core import persona_loader


def _make_persona(root, name: str, text: str, display_name: str | None = None) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "persona.md").write_text(text, encoding="utf-8")
    if display_name:
        (root / name / "meta.json").write_text(
            f'{{"display_name": "{display_name}"}}',
            encoding="utf-8",
        )


def _names(body) -> list[str]:
    return [item["name"] for item in body["available"]]


def test_get_persona_returns_active_available_and_content(client):
    body = client.get("/api/v1/persona").json()

    assert body["active"] == "companion"
    assert body["active_display_name"] == "行动搭档"
    assert "companion" in _names(body)
    assert "template" in _names(body)
    assert "行动搭档" in body["content"]


def test_activate_unknown_persona_returns_404(client):
    response = client.post("/api/v1/persona/activate", json={"name": "nope"})

    assert response.status_code == 404


def test_create_then_activate(client, monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。", "默认")

    created = client.post(
        "/api/v1/persona",
        json={
            "name": "new_her",
            "display_name": "新她",
            "content": "你是新的人格。",
            "activate": True,
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["active"] == "new_her"
    assert body["active_display_name"] == "新她"
    assert "new_her" in _names(body)
    assert body["content"] == "你是新的人格。"


def test_display_name_falls_back_to_directory_name(client, monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")
    _make_persona(tmp_path, "no_meta", "没有 meta.json 的人格。")

    body = client.post("/api/v1/persona/activate", json={"name": "no_meta"}).json()

    assert body["active_display_name"] == "no_meta"


def test_create_rejects_duplicate(client, monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")

    first = client.post("/api/v1/persona", json={"name": "dup", "content": "甲"})
    assert first.status_code == 201

    second = client.post("/api/v1/persona", json={"name": "dup", "content": "乙"})
    assert second.status_code == 400


def test_create_rejects_path_traversal_name(client):
    response = client.post(
        "/api/v1/persona",
        json={"name": "../evil", "content": "x"},
    )

    assert response.status_code == 422


def test_activate_switches_without_restart(client, monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")
    _make_persona(tmp_path, "other", "另一个人。", "另一个她")

    switched = client.post("/api/v1/persona/activate", json={"name": "other"})

    assert switched.status_code == 200
    assert switched.json()["active"] == "other"
    assert switched.json()["active_display_name"] == "另一个她"
    assert switched.json()["content"] == "另一个人。"


# ---------- 编辑人格（界面上的"编辑"） ----------


def test_edit_content_round_trip(client, monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "第一版人格，够十个字了。")

    body = client.put(
        "/api/v1/persona/content",
        json={"content": "改过的人格：他说话很短，从不解释。"},
    ).json()

    assert "改过的人格" in body["content"]
    # 再 GET 一次，确认**落盘**了，不是只改了内存
    assert "改过的人格" in client.get("/api/v1/persona").json()["content"]


def test_edit_keeps_a_backup(client, monkeypatch, tmp_path):
    """★ 覆盖前必须留备份 —— 这是**允许应用内编辑的前提**。

    原设计不许应用内编辑，理由是"避免应用内误覆盖"。
    备份满足那个目的之后，编辑器才不是风险。
    """
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "第一版人格，够十个字了。")

    client.put(
        "/api/v1/persona/content",
        json={"content": "第二版人格，也够十个字。"},
    )

    backup = tmp_path / "companion" / "persona.md.bak"
    assert backup.exists()
    assert "第一版人格" in backup.read_text(encoding="utf-8")


def test_edit_rejects_a_too_short_body(client, monkeypatch, tmp_path):
    """空人格会让她退回客服腔 —— 那正是这份文件唯一要防的事。"""
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "第一版人格，够十个字了。")

    response = client.put("/api/v1/persona/content", json={"content": "短"})

    assert response.status_code == 422
