"""S5「她眼中的我」API（代际链）。"""


def _observation(client, content: str, source: str) -> dict:
    response = client.post(
        "/api/v1/memory/observations",
        json={"content": content, "source_text": source},
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


def test_lineages_endpoint_returns_history(client):
    first_evidence = _observation(client, "甲", "甲原话")
    second_evidence = _observation(client, "乙", "乙原话")
    first = _impression(client, [first_evidence["id"], second_evidence["id"]])

    second = client.post(
        f"/api/v1/memory/impressions/{first['id']}/revise",
        json={"content": "后来我觉得是任务不够清楚"},
    ).json()

    lineages = client.get("/api/v1/memory/impression-lineages").json()

    assert len(lineages) == 1
    lineage = lineages[0]
    assert lineage["current_id"] == second["id"]
    assert lineage["generation"] == 2
    assert [entry["content"] for entry in lineage["history"]] == [
        "你写作启动困难",
        "后来我觉得是任务不够清楚",
    ]
    assert lineage["history"][-1]["is_current"] is True
    assert lineage["history"][0]["is_current"] is False


def test_lineages_include_evidence_with_source_text(client):
    evidence = _observation(client, "第一次没写完", "我明天一定写完")
    _impression(client, [evidence["id"]])

    lineage = client.get("/api/v1/memory/impression-lineages").json()[0]

    assert lineage["current"]["evidence"][0]["source_text"] == "我明天一定写完"
    assert lineage["current"]["evidence"][0]["local_date"]


def test_lineages_are_empty_without_impressions(client):
    assert client.get("/api/v1/memory/impression-lineages").json() == []


def test_lineages_path_does_not_collide_with_the_id_route(client):
    """`/impression-lineages` 不能被 `/impressions/{id}` 当 id 吃掉。"""
    response = client.get("/api/v1/memory/impression-lineages")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
