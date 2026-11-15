"""REST JSON 响应必须声明 UTF-8，避免中文客户端解码乱码。"""


def test_health_declares_utf8(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert "charset=utf-8" in response.headers["content-type"].lower()


def test_chat_api_declares_utf8(client, monkeypatch):
    from app.services import chat_service

    class StubProvider:
        available = True
        last_usage = None

        def chat(self, messages, temperature=None, json_mode=False):
            return "你好，世界。"

    monkeypatch.setattr(chat_service, "_provider", StubProvider())
    response = client.post("/api/v1/chat", json={"content": "你好"})

    assert response.status_code == 200
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert "你好，世界。" in response.text
