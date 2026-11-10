"""The API can continue a durable Supervisor run after losing all process state."""
from fastapi.testclient import TestClient

from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import FAKE_EMAIL
from app.api import supervisor_routes as routes
from app.llm.fake import FakeLlm
from app.main import app
from app.orchestration.supervisor_start import wire_supervisor
from app.orchestration.tool_state import SharedToolState
from schema.schemas import Agent1Input, Agent1Output
from test_agent3_agent import a3_input, a3_payload


def forget_process_state(monkeypatch):
    for name in ("_sessions", "_step_locks", "_progress"):
        monkeypatch.setattr(routes, name, {})
    routes.local_store.cache_clear()


def prepare(monkeypatch, tmp_path, a3_input, a3_payload, decisions, needs=()):
    forget_process_state(monkeypatch)
    monkeypatch.setattr(routes.settings, "prototype_supervisor_db", tmp_path / "supervisor.sqlite3")
    inp = Agent1Input(context=a3_input.context, received_at=a3_input.received_at,
                      sender_role="PM", message_text="합성 저장 검증")
    state = SharedToolState(Agent2CaseSnapshot(state_revision=0, context=inp.context,
        message_text=inp.message_text, a1_result_id="a1", items=a3_input.items, a2_result=a3_input.a2_result),
        inp, a3_input.rate_card_id)
    state.a1_result = Agent1Output(context=inp.context, status="READY", items=a3_input.items)
    state.replace_needs("agent2", list(needs))
    workers = {"agent1": None, "agent2": None, "agent3": LlmAgent3(FakeLlm([a3_payload])),
               "llm": FakeLlm(decisions), "draft_llm": FakeLlm([FAKE_EMAIL])}
    monkeypatch.setattr(routes, "_workers", lambda provider: workers)
    monkeypatch.setattr(routes, "start_supervisor", lambda *a, **kw: wire_supervisor(state, **kw))
    client = TestClient(app)
    base = "/prototype/supervisor/cases"
    response = client.post(base, json={"input": inp.model_dump(mode="json"), "rate_card_id": a3_input.rate_card_id})
    assert response.status_code == 200, response.text
    return client, f"{base}/{inp.context.case_id}"


def test_pending_question_and_answer_survive_restart(monkeypatch, tmp_path, a3_input, a3_payload):
    client, case = prepare(monkeypatch, tmp_path, a3_input, a3_payload,
        [{"action": "ask_pm", "args": {"question_id": "Q1"}, "reason": "조건 확인"}],
        [{"question_id": "Q1", "kind": "client_input", "target": "pm", "topic": "조건 확인", "blocking": True}])
    pending = client.post(f"{case}/step").json()["state"]
    forget_process_state(monkeypatch)
    assert client.get(case).json()["question"] == pending["question"]
    response = client.post(f"{case}/answer", json={"question_id": "Q1", "text": "사진은 제외합니다.",
                                                   "state_revision": pending["state_revision"]})
    assert response.status_code == 200, response.text
    forget_process_state(monkeypatch)
    assert client.get(case).json()["question"] is None
    assert routes._session(a3_input.context.case_id).runtime.state.a2.answers[-1].text == "사진은 제외합니다."


def test_final_review_uses_restored_agent3_session(monkeypatch, tmp_path, a3_input, a3_payload):
    ids = [item.item_id for item in a3_input.items]
    client, case = prepare(monkeypatch, tmp_path, a3_input, a3_payload, [
        {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "잠정 산정"},
        {"action": "draft_documents", "args": {"kind": "negotiation_email", "item_ids": ids}, "reason": "고객 문서"},
    ])
    assert client.post(f"{case}/step").json()["event"]["status"] == "ready"
    before = client.post(f"{case}/step").json()["state"]
    assert before["ready_for_review"]
    forget_process_state(monkeypatch)
    after = client.get(case).json()
    assert after == before
    approved = client.post(f"{case}/review", json={"decision": "approve", "state_revision": after["state_revision"],
        "expected_snapshot_hash": after["agent3"]["snapshot_hash"], "acknowledge_unknown_schedule": True})
    assert approved.status_code == 200, approved.text
    forget_process_state(monkeypatch)
    assert client.get(case).json()["review"]["decision"] == "approve"
