"""S4 记忆库 API：L3 印象。"""


def _observation(client, content: str, source: str, tag_ids: list[int] | None = None) -> dict:
    response = client.post(
        "/api/v1/memory/observations",
        json={"content": content, "source_text": source, "tag_ids": tag_ids or []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _impression(client, evidence_ids: list[int], content: str = "你写作启动困难") -> dict:
    response = client.post(
        "/api/v1/memory/impressions",
        json={"content": content, "evidence_ids": evidence_ids},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_impression_endpoints_round_trip(client):
    first = _observation(client, "第一次没写完", "我明天一定写完")
    second = _observation(client, "第二次没写完", "这次真的要写了")

    impression = _impression(client, [first["id"], second["id"]])

    assert impression["generation"] == 1
    assert impression["attitude"] == "none"
    assert impression["needs_revision"] is False
    assert [e["id"] for e in impression["evidence"]] == [first["id"], second["id"]]

    listed = client.get("/api/v1/memory/impressions").json()
    assert [i["id"] for i in listed] == [impression["id"]]


def test_impression_requires_evidence(client):
    response = client.post(
        "/api/v1/memory/impressions",
        json={"content": "无依据的判断", "evidence_ids": []},
    )

    assert response.status_code == 422


def test_unknown_evidence_returns_404(client):
    response = client.post(
        "/api/v1/memory/impressions",
        json={"content": "x", "evidence_ids": [999]},
    )

    assert response.status_code == 404


def test_dispute_endpoint_keeps_the_impression(client):
    first = _observation(client, "甲", "甲原话")
    impression = _impression(client, [first["id"]])

    response = client.post(
        f"/api/v1/memory/impressions/{impression['id']}/attitude",
        json={"attitude": "disputed"},
    )

    assert response.status_code == 200
    assert response.json()["attitude"] == "disputed"
    assert response.json()["content"] == "你写作启动困难"  # 判断不消失


def test_invalid_attitude_returns_422(client):
    first = _observation(client, "甲", "甲原话")
    impression = _impression(client, [first["id"]])

    response = client.post(
        f"/api/v1/memory/impressions/{impression['id']}/attitude",
        json={"attitude": "deleted"},
    )

    assert response.status_code == 422


def test_counterexamples_then_revision(client):
    first = _observation(client, "甲", "甲原话")
    impression = _impression(client, [first["id"]])

    response = None
    for expected in (1, 2, 3):
        response = client.post(
            f"/api/v1/memory/impressions/{impression['id']}/counterexamples",
            json={},
        )
        assert response.status_code == 200
        assert response.json()["counterexamples"] == expected

    assert response.json()["needs_revision"] is True

    revised = client.post(
        f"/api/v1/memory/impressions/{impression['id']}/revise",
        json={"content": "可能是任务不够清楚"},
    )
    assert revised.status_code == 200
    assert revised.json()["generation"] == 2

    # 旧版本还在，并被标为已取代
    old = client.get(f"/api/v1/memory/impressions/{impression['id']}").json()
    assert old["status"] == "superseded"
    assert old["superseded_by"] == revised.json()["id"]


def test_deleting_the_last_evidence_returns_400(client):
    first = _observation(client, "只此一次", "原话")
    _impression(client, [first["id"]])

    response = client.delete(f"/api/v1/memory/observations/{first['id']}")

    assert response.status_code == 400


def test_supporting_observations_endpoint(client):
    tag = client.post(
        "/api/v1/memory/tags",
        json={"name": "写作", "dimension": "pattern"},
    ).json()

    for i in (1, 2):
        _observation(client, f"第 {i} 次", f"原话 {i}", [tag["id"]])

    body = client.get(f"/api/v1/memory/tags/{tag['id']}/supporting").json()
    assert body["count"] == 2
    assert body["ready"] is False

    _observation(client, "第 3 次", "原话 3", [tag["id"]])

    body = client.get(f"/api/v1/memory/tags/{tag['id']}/supporting").json()
    assert body["count"] == 3
    assert body["ready"] is True


def test_impressions_can_exclude_superseded_generations(client):
    first = _observation(client, "甲", "甲原话")
    impression = _impression(client, [first["id"]])
    client.post(
        f"/api/v1/memory/impressions/{impression['id']}/revise",
        json={"content": "新判断"},
    )

    all_generations = client.get("/api/v1/memory/impressions").json()
    assert len(all_generations) == 2

    current_only = client.get(
        "/api/v1/memory/impressions?include_superseded=false"
    ).json()
    assert len(current_only) == 1
    assert current_only[0]["generation"] == 2
