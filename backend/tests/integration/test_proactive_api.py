"""S8 主动联系 API。"""

from datetime import datetime, timedelta, timezone

from app.core.time_utils import to_utc_iso
from app.database.connection import get_connection
from app.workflows import proactive


def _days_ago(days: int) -> datetime:
    """正好 N 天前（当地自然日差也是 N，所以分档是确定的）。"""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _user_message_at(moment: datetime) -> None:
    with get_connection() as conn:
        conversation_id = conn.execute(
            "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, model, metadata_json, created_at
            ) VALUES (?, 'user', '在吗', NULL, NULL, ?)
            """,
            (conversation_id, to_utc_iso(moment)),
        )


class _FakeProvider:
    available = True
    last_usage = None

    def chat(self, messages, temperature=None):
        return "在忙？"


def test_tick_does_nothing_without_history(client):
    body = client.post("/api/v1/proactive/tick").json()

    assert body["did"] is None


def test_tick_sends_the_three_day_signal(client, monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _FakeProvider())
    _user_message_at(_days_ago(5))  # 5 天 → 落在 3 天档

    body = client.post("/api/v1/proactive/tick").json()

    assert body["did"]["kind"] == "silence_3d"
    assert body["did"]["delivered"] is True


def test_tick_writes_the_essay_but_does_not_deliver_it(client, monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _FakeProvider())
    _user_message_at(_days_ago(31))

    body = client.post("/api/v1/proactive/tick").json()

    assert body["did"]["kind"] == "essay"
    assert body["did"]["delivered"] is False

    pending = client.get("/api/v1/proactive/pending").json()
    assert [item["id"] for item in pending] == [body["did"]["id"]]


def test_deliver_endpoint(client, monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _FakeProvider())
    _user_message_at(_days_ago(31))
    essay = client.post("/api/v1/proactive/tick").json()["did"]

    delivered = client.post(
        f"/api/v1/proactive/contacts/{essay['id']}/deliver"
    ).json()

    assert delivered["delivered"] is True
    assert client.get("/api/v1/proactive/pending").json() == []


def test_contacts_endpoint_lists_history(client, monkeypatch):
    monkeypatch.setattr(proactive, "_provider", _FakeProvider())
    _user_message_at(_days_ago(5))
    client.post("/api/v1/proactive/tick")

    contacts = client.get("/api/v1/proactive/contacts").json()

    assert len(contacts) == 1
    assert contacts[0]["kind"] == "silence_3d"


def test_deliver_unknown_contact_returns_404(client):
    assert client.post("/api/v1/proactive/contacts/999/deliver").status_code == 404


def test_health_reports_the_proactive_flag(client):
    body = client.get("/api/v1/health").json()

    assert "proactive_enabled" in body
