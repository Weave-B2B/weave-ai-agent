from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event

import pytest
from fastapi.testclient import TestClient
from test_agent3_agent import a3_input

from app.api import supervisor_routes as routes
from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot
from app.llm.fake import FakeLlm
from app.main import app
from app.orchestration.progress import record_activity
from app.orchestration.supervisor_runtime import SupervisorRuntime
from app.orchestration.tool_state import SharedToolState
from app.tools.trace import traced
from schema.schemas import Agent1Input, Agent1Output


@pytest.fixture(autouse=True)
def isolated_sessions(monkeypatch):
    for name in ("_sessions", "_step_locks", "_progress"):
        monkeypatch.setattr(routes, name, {})
    monkeypatch.setattr(routes.settings, "prototype_supervisor_db", None)
    routes.local_store.cache_clear()


@pytest.mark.parametrize("provider", [None, "local"])
def test_session_routes_step_question_and_revision(a3_input, monkeypatch, provider):
    ctx = a3_input.context
    inp = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="합성 요청")
    snapshot = Agent2CaseSnapshot(state_revision=0, context=ctx, message_text=inp.message_text,
        a1_result_id="a1-result", items=a3_input.items, a2_result=a3_input.a2_result)
    state = SharedToolState(snapshot, inp, a3_input.rate_card_id)
    state.a1_result = Agent1Output(context=ctx, status="READY", items=a3_input.items)
    state.last_needs = [{"question_id": "Q1", "kind": "client_input", "target": "pm", "blocking": True, "topic": "범위 확인", "item_ids": [a3_input.items[0].item_id]}]
    llm = FakeLlm([{"action": "ask_pm", "args": {"question_id": "Q1"}, "reason": "확인 필요"}])
    runtime = SupervisorRuntime(state, None, None, llm)
    monkeypatch.setattr(routes.settings, "llm_model", "unrelated-api-model")
    monkeypatch.setattr(routes.settings, "ollama_model", "unrelated-local-model")
    monkeypatch.setattr(routes.settings, "ollama_base_url", "")
    monkeypatch.setattr(routes.settings, "llm_base_url", "https://example.invalid")

    def build_test_llm(config):
        if provider == "local":
            assert config.llm_provider == "ollama"
            assert config.ollama_model == "gpt-oss:20b"
            assert config.ollama_base_url == "http://localhost:11434"
        else:
            assert config.llm_provider == "openai"
            assert config.llm_model == "gpt-6-luna"
        return llm

    monkeypatch.setattr(routes, "build_llm", build_test_llm)
    monkeypatch.setattr(routes, "start_supervisor", lambda *a, **kw: runtime)
    client = TestClient(app)
    base = "/prototype/supervisor/cases"
    payload = {"input": inp.model_dump(mode="json"), "rate_card_id": a3_input.rate_card_id}
    if provider is not None:
        payload["provider"] = provider
    response = client.post(base, json=payload)
    assert response.status_code == 200
    case = ctx.case_id
    assert client.post(base, json={"input": inp.model_dump(mode="json"), "rate_card_id": a3_input.rate_card_id}).status_code == 409
    response = client.post(f"{base}/{case}/step")
    assert response.json()["event"]["status"] == "waiting_input"
    assert client.post(f"{base}/{case}/answer", json={"question_id": "Q1", "text": "예", "state_revision": 0}).status_code == 409
    response = client.post(f"{base}/{case}/answer", json={"question_id": "Q1", "text": "예", "state_revision": 1})
    assert response.status_code == 200 and response.json()["state_revision"] == 2
    assert client.get(f"{base}/{case}").json()["question"] is None


def test_progress_is_readable_during_start_and_tool_execution(a3_input, monkeypatch):
    inp = Agent1Input(context=a3_input.context, received_at=a3_input.received_at,
                      sender_role="PM", message_text="합성 요청")
    case_id = inp.context.case_id
    snapshot = Agent2CaseSnapshot(state_revision=0, context=inp.context, message_text=inp.message_text,
                                 a1_result_id="a1-result", items=a3_input.items)
    state = SharedToolState(snapshot, inp, a3_input.rate_card_id)
    state.a1_result = Agent1Output(context=inp.context, status="READY", items=a3_input.items)
    start_running, start_release, tool_running, tool_release = (Event() for _ in range(4))

    @traced("search_docs")
    def search(query):
        tool_running.set()
        assert tool_release.wait(timeout=5)
        return [{"text": "계약 원문"}]

    class Session:
        history = []

        def step(self):
            record_activity("Agent 2", "범위 검토 중")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(copy_context().run, search, "내부 검색어").result()
            return {"action": "assess_scope", "status": "ready"}

    session = Session()
    session.state = state

    def start_supervisor(*args, **kwargs):
        start_running.set()
        assert start_release.wait(timeout=5)
        return session

    monkeypatch.setattr(routes, "build_llm", lambda config: FakeLlm([]))
    monkeypatch.setattr(routes, "start_supervisor", start_supervisor)
    base = "/prototype/supervisor/cases"
    progress_url = f"{base}/{case_id}/progress"
    payload = {"input": inp.model_dump(mode="json"), "rate_card_id": a3_input.rate_card_id}

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        start = executor.submit(client.post, base, json=payload)
        try:
            assert start_running.wait(timeout=5)
            events = executor.submit(client.get, progress_url).result(timeout=2).json()["events"]
            assert events[0]["agent"] == "Agent 1"
        finally:
            start_release.set()
        assert start.result(timeout=5).status_code == 200

        step = executor.submit(client.post, f"{base}/{case_id}/step")
        try:
            assert tool_running.wait(timeout=5)
            events = executor.submit(client.get, progress_url, params={"after": 1}).result(timeout=2).json()["events"]
            assert [e["seq"] for e in events] == [2, 3]
            parent, search_event = events
            assert parent["kind"] == "step" and parent["agent"] == "Agent 2"
            assert search_event["kind"] == "tool" and search_event["parent_seq"] == parent["seq"]
            assert "검색하고 있습니다" in search_event["title"]
            assert client.get(f"{base}/other/progress").json() == {"events": []}
        finally:
            tool_release.set()
        events = step.result(timeout=5).json()["state"]["progress"]
        assert [e["seq"] for e in events] == [1, 2, 3, 4]
        assert events[-1]["phase"] == "done" and events[-1]["matches"] == 1
        assert "계약 원문" not in str(events)
        assert "내부 검색어" not in str(events)
        assert client.get(progress_url, params={"after": 4}).json() == {"events": []}
