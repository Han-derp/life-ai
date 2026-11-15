from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.core.config import APP_TIMEZONE
from app.main import app
from app.services import chat_service, review_service


class FakeProvider:
    available = True

    def chat(self, messages, temperature=None):
        system = messages[0]["content"] if messages else ""
        if "Daily Review" in system or "严格 JSON" in system or "tomorrow_focus" in system:
            return (
                '{"summary":"今天的数据已经收好了。",'
                '"affirmation":"你完成了实际记录。",'
                '"main_issue":"还有一个DDL需要继续推进。",'
                '"tomorrow_focus":[{"text":"推进集成测试 DDL","deadline_id":1}]}'
            )
        return "我看到了。今天的日程、习惯和番茄都已经进到真实数据里了。"


def local_input(dt):
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def main():
    chat_service._provider = FakeProvider()
    review_service._provider = FakeProvider()

    now = datetime.now(ZoneInfo(APP_TIMEZONE))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200

        root = client.get("/")
        assert root.status_code == 200
        assert "Life AI" in root.text

        deadline = client.post(
            "/api/v1/deadlines",
            json={
                "title": "集成测试 DDL",
                "due_at_local": local_input(now + timedelta(days=1)),
                "priority": 1,
            },
        )
        assert deadline.status_code == 201, deadline.text
        deadline_id = deadline.json()["id"]

        schedule = client.post(
            "/api/v1/schedule",
            json={
                "title": "集成测试日程",
                "start_at_local": local_input(now - timedelta(minutes=10)),
                "end_at_local": local_input(now + timedelta(minutes=40)),
            },
        )
        assert schedule.status_code == 201, schedule.text
        schedule_id = schedule.json()["id"]
        completed_schedule = client.post(f"/api/v1/schedule/{schedule_id}/complete", json={})
        assert completed_schedule.status_code == 200
        assert completed_schedule.json()["completion_status"] == "completed"

        habit = client.post(
            "/api/v1/habits",
            json={
                "name": "测试喝水",
                "tracking_type": "numeric",
                "aggregation_mode": "sum",
                "unit": "ml",
                "target_value": 2000,
                "target_operator": ">=",
            },
        )
        assert habit.status_code == 201, habit.text
        habit_id = habit.json()["id"]
        assert client.post(f"/api/v1/habits/{habit_id}/log", json={"value_number": 500}).status_code == 200
        second = client.post(f"/api/v1/habits/{habit_id}/log", json={"value_number": 500})
        assert second.status_code == 200
        assert second.json()["current_number"] == 1000

        pomodoro = client.post(
            "/api/v1/pomodoro/start",
            json={"planned_minutes": 25, "custom_label": "集成测试专注"},
        )
        assert pomodoro.status_code == 201, pomodoro.text
        pomodoro_id = pomodoro.json()["id"]
        finished = client.post(f"/api/v1/pomodoro/{pomodoro_id}/finish", json={})
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "completed"

        timeline = client.get("/api/v1/timeline/today")
        assert timeline.status_code == 200
        assert any(x["source_type"] == "pomodoro" and x["source_id"] == pomodoro_id for x in timeline.json())

        context = client.get("/api/v1/context/today")
        assert context.status_code == 200, context.text
        data = context.json()
        assert any(x["id"] == deadline_id for x in data["deadlines"])
        assert any(x["id"] == habit_id and x["current_number"] == 1000 for x in data["habits"])
        assert data["pomodoro"]["count"] >= 1

        chat = client.post("/api/v1/chat", json={"content": "我今天怎么样？"})
        assert chat.status_code == 200, chat.text
        assert chat.json()["assistant_message"]["role"] == "assistant"
        messages = client.get("/api/v1/chat/messages").json()
        assert messages[-2]["role"] == "user"
        assert messages[-1]["role"] == "assistant"

        review = client.post("/api/v1/review/today/generate", json={})
        assert review.status_code == 200, review.text
        review_data = review.json()
        assert review_data["stats"]["pomodoro"]["count"] >= 1
        assert review_data["ai_summary"] == "今天的数据已经收好了。"

        confirmed = client.post(
            "/api/v1/review/today/confirm",
            json={"tomorrow_focus": review_data["tomorrow_focus"]},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"

    print("✓ V1 integration test passed")


if __name__ == "__main__":
    main()
