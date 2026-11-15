"""S7 规则 API。"""


def _create(client, content: str = "23:00 之后不主动联系", **kwargs) -> dict:
    response = client.post("/api/v1/rules", json={"content": content, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_and_delete(client):
    rule = _create(client, source_text="以后十一点以后别找我")

    assert rule["source"] == "manual"
    assert rule["source_text"] == "以后十一点以后别找我"

    listed = client.get("/api/v1/rules").json()
    assert [item["id"] for item in listed] == [rule["id"]]

    assert client.delete(f"/api/v1/rules/{rule['id']}").status_code == 204
    assert client.get("/api/v1/rules").json() == []


def test_same_day_rule_is_not_permanent(client):
    """"我今天不想被打扰" 生成当日规则，不是永久规则。"""
    rule = _create(client, "今天别打扰我", expires_at_local="2026-06-01")

    assert rule["is_permanent"] is False
    assert rule["expires_at"] == "2026-06-01T16:00:00Z"


def test_patch_takes_ownership_of_the_rule(client):
    chat_rule = client.post(
        "/api/v1/rules/from-chat",
        json={"content": "十点后别找我", "source_text": "以后十点以后别找我"},
    ).json()["rule"]
    assert chat_rule["source"] == "chat"

    patched = client.patch(
        f"/api/v1/rules/{chat_rule['id']}",
        json={"content": "十一点后别找我"},
    ).json()

    assert patched["content"] == "十一点后别找我"
    assert patched["source"] == "manual"


def test_manual_rule_blocks_the_same_chat_rule(client):
    manual = _create(client, "十一点后别找我", source_text="我说的")

    result = client.post(
        "/api/v1/rules/from-chat",
        json={"content": "十一点后别找我", "source_text": "以后十一点以后别找我"},
    ).json()

    assert result["created"] is False
    assert result["overridden_by_manual"] is True
    assert result["rule"]["id"] == manual["id"]
    assert result["rule"]["source_text"] == "我说的"
    assert len(client.get("/api/v1/rules").json()) == 1


def test_chat_rule_without_source_is_rejected(client):
    response = client.post(
        "/api/v1/rules/from-chat",
        json={"content": "别在晚上找我"},
    )

    assert response.status_code == 422


def test_blank_source_text_is_rejected(client):
    response = client.post(
        "/api/v1/rules/from-chat",
        json={"content": "别在晚上找我", "source_text": "   "},
    )

    assert response.status_code == 400


def test_invalid_status_returns_422(client):
    rule = _create(client)

    response = client.patch(f"/api/v1/rules/{rule['id']}", json={"status": "nope"})

    assert response.status_code == 422


def test_unknown_rule_returns_404(client):
    assert client.get("/api/v1/rules/999").status_code == 404


def test_include_inactive_filter(client):
    _create(client, "过期规则", expires_at_local="2000-01-01")

    assert client.get("/api/v1/rules").json() != []
    assert client.get("/api/v1/rules?include_inactive=false").json() == []


def test_invalid_expiry_returns_400(client):
    response = client.post(
        "/api/v1/rules",
        json={"content": "坏期限", "expires_at_local": "not-a-date"},
    )

    assert response.status_code == 400
