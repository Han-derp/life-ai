"""S7 承诺 API。"""

QUIET = {"quiet_hours": {"start": "23:00", "end": "08:00"}}


def _create(client, **kwargs) -> dict:
    payload = {"content": "行。那你也别十一点后还醒着。", **kwargs}
    response = client.post("/api/v1/commitments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_list(client):
    commitment = _create(client, source_text="以后十一点以后别找我", structured=QUIET)

    assert commitment["window_text"] == "23:00 – 08:00 不主动找你"
    assert commitment["source_text"] == "以后十一点以后别找我"

    listed = client.get("/api/v1/commitments").json()
    assert [item["id"] for item in listed] == [commitment["id"]]


def test_check_endpoint_finds_violations(client):
    commitment = _create(client, structured=QUIET)

    response = client.post(
        f"/api/v1/commitments/{commitment['id']}/check",
        json={"moments": ["2026-06-01T15:30:00Z", "2026-06-01T14:00:00Z"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["checkable"] is True
    assert body["checked"] == 2
    assert len(body["violations"]) == 1


def test_check_endpoint_reports_unstructured_as_uncheckable(client):
    commitment = _create(client)

    body = client.post(
        f"/api/v1/commitments/{commitment['id']}/check",
        json={"moments": []},
    ).json()

    assert body["checkable"] is False


def test_revise_keeps_the_old_version(client):
    first = _create(client, source_text="以后十一点以后别找我")

    second = client.post(
        f"/api/v1/commitments/{first['id']}/revise",
        json={"content": "行。那十二点。"},
    ).json()

    assert second["id"] != first["id"]
    assert second["content"] == "行。那十二点。"

    old = client.get(f"/api/v1/commitments/{first['id']}").json()
    assert old["status"] == "changed"
    assert old["superseded_by"] == second["id"]


def test_revoke_endpoint(client):
    commitment = _create(client)

    revoked = client.post(
        f"/api/v1/commitments/{commitment['id']}/revoke",
        json={},
    ).json()

    assert revoked["status"] == "revoked"
    assert revoked["in_effect"] is False


def test_invalid_structured_key_returns_400(client):
    response = client.post(
        "/api/v1/commitments",
        json={"content": "x", "structured": {"nope": 1}},
    )

    assert response.status_code == 400


def test_chat_commitment_requires_source_text(client):
    response = client.post(
        "/api/v1/commitments/from-chat",
        json={"content": "行。"},
    )

    assert response.status_code == 422


def test_blank_source_text_returns_400(client):
    response = client.post(
        "/api/v1/commitments/from-chat",
        json={"content": "行。", "source_text": "   "},
    )

    assert response.status_code == 400


def test_manual_commitment_blocks_the_same_chat_commitment(client):
    manual = _create(client)

    result = client.post(
        "/api/v1/commitments/from-chat",
        json={"content": manual["content"], "source_text": "以后十一点以后别找我"},
    ).json()

    assert result["created"] is False
    assert result["overridden_by_manual"] is True


def test_unknown_commitment_returns_404(client):
    assert client.get("/api/v1/commitments/999").status_code == 404
