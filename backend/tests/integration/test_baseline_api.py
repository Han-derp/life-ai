"""S8 · B 类基线预警 API + 承诺违规核对接上真实数据。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import APP_TIMEZONE
from app.core.time_utils import to_utc_iso
from app.database.connection import get_connection


def _local(day: str, hhmm: str) -> datetime:
    naive = datetime.fromisoformat(f"{day}T{hhmm}")
    return naive.replace(tzinfo=ZoneInfo(APP_TIMEZONE)).astimezone(timezone.utc)


def _deadline_due_in(hours: int, title: str = "交报告") -> None:
    moment = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO deadlines (title, due_at, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                to_utc_iso(moment + timedelta(hours=hours)),
                to_utc_iso(moment),
                to_utc_iso(moment),
            ),
        )


def _proactive_contact_at(moment: datetime, kind: str = "care") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO proactive_contacts (
                kind, content, slot, created_at, delivered_at, responded_at
            ) VALUES (?, '在忙？', NULL, ?, ?, NULL)
            """,
            (kind, to_utc_iso(moment), to_utc_iso(moment)),
        )


def _commitment(client, start: str, end: str) -> dict:
    response = client.post(
        "/api/v1/commitments",
        json={
            "content": "行，十一点后不找你。",
            "structured": {"quiet_hours": {"start": start, "end": end}},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------- 基线预警 API ----------


class _FixedProvider:
    """基线预警的文案**必须可控**。

    原来这个测试不拦 provider，于是：
    - 没配 API Key 时走兜底文案，恰好以「我注意到」开头 → 绿
    - **一旦配上 Key，它就会真的调模型**，断言变成"看模型今天的心情" ✗

    测试不该有这种依赖。这里把它钉死；
    "提示词里有没有写清这个要求"由下面那个**静态**测试来守。
    """

    available = True
    last_usage = None

    def __init__(self, text: str = "我注意到那件事快到期了，你一直没动，怎么了？"):
        self.text = text

    def chat(self, messages, temperature=None, json_mode=False):
        return self.text


def test_baseline_scan_voice_and_dismiss(client, monkeypatch):
    from app.workflows import baseline

    monkeypatch.setattr(baseline, "_provider", _FixedProvider())
    _deadline_due_in(24)

    recorded = client.post("/api/v1/proactive/baseline/scan").json()

    assert len(recorded) == 1
    assert recorded[0]["kind"] == "deadline_stall"
    assert recorded[0]["content"] is None  # 还没说出口
    alert_id = recorded[0]["id"]

    listed = client.get("/api/v1/proactive/baseline").json()
    assert [item["id"] for item in listed] == [alert_id]

    voiced = client.post(f"/api/v1/proactive/baseline/{alert_id}/voice").json()
    assert voiced["voiced"] is True
    assert voiced["content"].startswith("我注意到")

    dismissed = client.post(f"/api/v1/proactive/baseline/{alert_id}/dismiss").json()
    assert dismissed["dismissed_at"] is not None
    assert client.get("/api/v1/proactive/baseline").json() == []


def test_the_prompt_tells_the_model_to_open_with_noticing():
    """**静态**守提示词，而不是靠"看模型今天心情"。

    真实模型第一次跑出来的是「报告别硬扛，先写个开头，我陪你。」——
    完全没有「我注意到……怎么了？」的开头，
    因为 `proactive.md` 里**压根没写** `late_night` / `deadline_stall` 这两种类型。
    这是配了 API Key 之后才暴露出来的。
    """
    from app.core.prompt_loader import load_prompt

    prompt = load_prompt("proactive.md")

    for kind in ("late_night", "deadline_stall"):
        assert kind in prompt, f"proactive.md 里没有 {kind} 的写法要求"
    assert "我注意到" in prompt


def test_baseline_nothing_to_report(client):
    assert client.post("/api/v1/proactive/baseline/scan").json() == []


def test_baseline_voice_unknown_returns_404(client):
    assert client.post("/api/v1/proactive/baseline/999/voice").status_code == 404


def test_tick_also_scans_the_baseline(client):
    """手动 tick 和后台调度做的是同一件事。"""
    _deadline_due_in(24)

    client.post("/api/v1/proactive/tick")

    assert client.get("/api/v1/proactive/baseline").json() != []


# ---------- 承诺违规核对接上真实数据（S7 遗留） ----------


def test_commitment_violations_use_real_proactive_contacts(client):
    commitment = _commitment(client, "23:00", "08:00")

    # 她在这条承诺的窗口里主动找过你（当地 23:30）
    _proactive_contact_at(_local("2026-06-01", "23:30"))

    body = client.get(f"/api/v1/commitments/{commitment['id']}/violations").json()

    assert body["checkable"] is True
    assert body["checked"] == 1
    assert len(body["violations"]) == 1


def test_no_proactive_contacts_means_no_violations(client):
    commitment = _commitment(client, "23:00", "08:00")

    body = client.get(f"/api/v1/commitments/{commitment['id']}/violations").json()

    assert body["checked"] == 0
    assert body["violations"] == []


def test_quiet_contact_outside_the_window_is_not_a_violation(client):
    commitment = _commitment(client, "23:00", "08:00")

    _proactive_contact_at(_local("2026-06-01", "15:00"))

    body = client.get(f"/api/v1/commitments/{commitment['id']}/violations").json()

    assert body["checked"] == 1
    assert body["violations"] == []


def test_essay_is_not_counted_as_her_contacting_you(client):
    """小作文是写下来、等你回来看的，不算"她找上门"——不能拿它冤枉她。"""
    commitment = _commitment(client, "23:00", "08:00")
    _proactive_contact_at(_local("2026-06-01", "23:30"), kind="essay")

    body = client.get(f"/api/v1/commitments/{commitment['id']}/violations").json()

    assert body["checked"] == 0
