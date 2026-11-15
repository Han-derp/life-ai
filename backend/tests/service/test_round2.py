from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.services import pomodoro_service as timer, action_plan_service as plans, action_step_service as steps
from app.schemas.pomodoro import PomodoroStart
from app.schemas.action_plan import ActionPlanCreate, ActionStep
from app.database.connection import get_connection
from app.services.execution_stats_service import get_execution_stats
from app.core.exceptions import DomainValidationError, ConflictError


def plan():
    p=plans.create_plan(ActionPlanCreate(title='test',steps=[ActionStep(title='one',minutes=10)]))
    plans.confirm_plan(p['id'])
    return p['id'],p['executable_steps'][0]['id']


def test_pause_resume_cancel_and_retry(client,monkeypatch):
    pid,sid=plan()
    now=[datetime(2026,11,14,1,tzinfo=timezone.utc)]
    monkeypatch.setattr(timer,'utc_now',lambda:now[0])
    t=timer.start_pomodoro(PomodoroStart(action_step_id=sid))
    now[0]+=timedelta(seconds=40)
    assert timer.pause_pomodoro(t['id'])['focus_seconds']==40
    timer.pause_pomodoro(t['id'])
    now[0]+=timedelta(seconds=120)
    assert timer.get_current_pomodoro()['focus_seconds']==40
    with pytest.raises(ConflictError): timer.start_pomodoro(PomodoroStart(custom_label='other'))
    timer.resume_pomodoro(t['id'])
    timer.resume_pomodoro(t['id'])
    now[0]+=timedelta(seconds=30)
    result=timer.cancel_pomodoro(t['id'])
    assert result['focus_seconds']==70 and result['elapsed_seconds']==190
    timer.cancel_pomodoro(t['id'])
    row=steps.list_steps(pid)[0]
    assert row['actual_seconds']==70 and row['pomodoro_count']==1 and row['interruption_count']==1
    assert row['status']!='completed'
    with get_connection() as c:
        assert c.execute('SELECT COUNT(*) FROM timeline_events WHERE source_id=?',(t['id'],)).fetchone()[0]==1
    with pytest.raises(DomainValidationError): timer.finish_pomodoro(t['id'])


def test_finish_while_paused_and_stats(client,monkeypatch):
    pid,sid=plan()
    now=[datetime(2026,11,14,1,tzinfo=timezone.utc)]
    monkeypatch.setattr(timer,'utc_now',lambda:now[0])
    t=timer.start_pomodoro(PomodoroStart(action_step_id=sid))
    now[0]+=timedelta(seconds=60);timer.pause_pomodoro(t['id'])
    now[0]+=timedelta(seconds=60)
    assert timer.finish_pomodoro(t['id'])['focus_seconds']==60
    steps.adjust_step(pid,sid,minutes=50)
    steps.complete_step(pid,sid)
    with get_connection() as c:
        c.execute("UPDATE action_plan_steps SET completed_at='2026-11-14T02:00:00Z' WHERE id=?",(sid,))
    st=get_execution_stats('2026-11-14T00:00:00Z','2026-11-15T00:00:00Z')
    assert st['confirmed_estimate_seconds']==600
    assert st['deviation_seconds']==-540
    assert st['recorded_focus_seconds']==60


def test_materialize_concurrent_and_payload_conflict(client):
    pid,sid=plan()
    def run(_): return steps.materialize_step(pid,sid,kind='deadline',due_at='2026-12-01T20:00',materialize_key='retry')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=list(pool.map(run,range(2)))
    assert a['deadline_id']==b['deadline_id']
    with get_connection() as c: assert c.execute('SELECT COUNT(*) FROM deadlines').fetchone()[0]==1
    with pytest.raises(DomainValidationError):
        steps.materialize_step(pid,sid,kind='deadline',due_at='2026-12-02T20:00',materialize_key='retry')


def test_materialize_rolls_back_after_create(client):
    pid,sid=plan()
    with get_connection() as c:
        c.execute("CREATE TRIGGER fail_link BEFORE UPDATE OF deadline_id ON action_plan_steps BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(Exception,match='injected'):
        steps.materialize_step(pid,sid,kind='deadline',due_at='2026-12-01T20:00')
    with get_connection() as c: assert c.execute('SELECT COUNT(*) FROM deadlines').fetchone()[0]==0


def test_pause_routes(client):
    t=client.post('/api/v1/pomodoro/start',json={'custom_label':'test'}).json()
    assert client.post(f"/api/v1/pomodoro/{t['id']}/pause").json()['paused']
    assert not client.post(f"/api/v1/pomodoro/{t['id']}/resume").json()['paused']
