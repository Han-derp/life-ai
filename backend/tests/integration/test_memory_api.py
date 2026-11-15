"""S4 记忆库 API。"""


def _create_tag(client, name: str = "学习", dimension: str = "domain") -> dict:
    response = client.post(
        "/api/v1/memory/tags",
        json={"name": name, "dimension": dimension},
    )
    if response.status_code == 409:
        # migration 009 预置了默认标签（学习 / 回避…）—— 这些名字可能已存在，
        # 复用现成的，别让测试撞在种子数据上。
        tags = client.get("/api/v1/memory/tags").json()
        return next(tag for tag in tags if tag["name"] == name)

    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_list_observations(client):
    created = client.post(
        "/api/v1/memory/observations",
        json={
            "content": "背单词 120 分钟",
            "source_text": "今天背了两小时单词，有点累",
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_text"] == "今天背了两小时单词，有点累"
    assert body["raw_entry_id"] is not None
    assert body["tags"] == []

    listed = client.get("/api/v1/memory/observations")
    assert listed.status_code == 200
    assert [o["id"] for o in listed.json()] == [body["id"]]


def test_source_text_is_required(client):
    response = client.post(
        "/api/v1/memory/observations",
        json={"content": "没有出处"},
    )

    assert response.status_code == 422


def test_invalid_local_date_returns_400_not_500(client):
    """V1 遗留的缺陷模式（local_date 不校验 → 500），新端点不再犯。"""
    response = client.get("/api/v1/memory/observations?local_date=abc")

    assert response.status_code == 400


def test_delete_tag_keeps_the_observation(client):
    tag = _create_tag(client, "回避", "pattern")
    observation = client.post(
        "/api/v1/memory/observations",
        json={
            "content": "又一次推迟",
            "source_text": "我明天一定写完",
            "tag_ids": [tag["id"]],
        },
    ).json()
    assert [t["name"] for t in observation["tags"]] == ["回避"]

    deleted = client.delete(f"/api/v1/memory/tags/{tag['id']}")
    assert deleted.status_code == 204

    listed = client.get("/api/v1/memory/observations").json()
    assert [o["id"] for o in listed] == [observation["id"]]
    assert listed[0]["tags"] == []
    assert listed[0]["source_text"] == "我明天一定写完"


def test_put_observation_tags_replaces_the_set(client):
    state = _create_tag(client, "专注", "state")
    domain = _create_tag(client, "学习", "domain")
    observation = client.post(
        "/api/v1/memory/observations",
        json={"content": "x", "source_text": "y", "tag_ids": [state["id"]]},
    ).json()

    updated = client.put(
        f"/api/v1/memory/observations/{observation['id']}/tags",
        json={"tag_ids": [domain["id"]]},
    )

    assert updated.status_code == 200
    assert [t["name"] for t in updated.json()["tags"]] == ["学习"]


def test_duplicate_tag_returns_409(client):
    _create_tag(client, "学习", "domain")

    response = client.post(
        "/api/v1/memory/tags",
        json={"name": "学习", "dimension": "domain"},
    )

    assert response.status_code == 409


def test_unknown_tag_id_returns_404(client):
    response = client.post(
        "/api/v1/memory/observations",
        json={"content": "x", "source_text": "y", "tag_ids": [999]},
    )

    assert response.status_code == 404


def test_delete_observation_returns_204(client):
    observation = client.post(
        "/api/v1/memory/observations",
        json={"content": "x", "source_text": "y"},
    ).json()

    deleted = client.delete(f"/api/v1/memory/observations/{observation['id']}")

    assert deleted.status_code == 204
    assert client.get("/api/v1/memory/observations").json() == []


def test_invalid_tag_dimension_returns_422(client):
    response = client.post(
        "/api/v1/memory/tags",
        json={"name": "x", "dimension": "nope"},
    )

    assert response.status_code == 422


def test_tags_are_listed_with_usage_counts(client):
    tag = _create_tag(client, "学习", "domain")
    client.post(
        "/api/v1/memory/observations",
        json={"content": "甲", "source_text": "甲原话", "tag_ids": [tag["id"]]},
    )

    tags = client.get("/api/v1/memory/tags").json()

    # ⚠️ 不能断言"表里只有这一个" —— migration 009 预置了 18 个默认标签。
    # 这条测的是**用了几次就记几次**，不是"标签表有多干净"。
    used = next(item for item in tags if item["id"] == tag["id"])
    assert used["observation_count"] == 1
