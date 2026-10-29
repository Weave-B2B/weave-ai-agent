"""POST /agent2/assessments: fixture 모드, 실제 에이전트 경로(가짜 LLM), 오류 응답 매핑."""
import pytest
from fastapi.testclient import TestClient
from schema.schemas import AgentError

from app.agents.agent2.tool_agent import ToolAgent2
from app.agents.base import AgentFailure
from app.api import agent2_routes
from app.core.config import settings
from app.main import app
from test_agent2 import GOLD, INPUTS
from test_agent2_tool_agent import ScriptLLM

CASE = "CR-A-001"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "fixture")       # .env 값과 무관하게
    monkeypatch.setattr(settings, "agent2_mode", "fixture")
    cached = agent2_routes.get_agent2                       # 테스트가 monkeypatch로 바꿔도 원본 캐시를 비운다
    cached.cache_clear()
    yield TestClient(app)
    cached.cache_clear()


def _use(monkeypatch, agent):
    monkeypatch.setattr(agent2_routes, "get_agent2", lambda: agent)


def test_fixture_mode_returns_gold_with_issued_ids(client):
    r = client.post("/agent2/assessments", json=INPUTS[CASE])
    assert r.status_code == 200
    body = r.json()
    assert [a["label"] for a in body["assessments"]] == [a["label"] for a in GOLD[CASE]["assessments"]]
    assert body["meta"]["result_id"] and body["meta"]["created_at"]
    assert body["context"] == INPUTS[CASE]["context"]


def test_llm_mode_runs_tool_agent(client, monkeypatch):
    _use(monkeypatch, ToolAgent2(ScriptLLM(GOLD[CASE])))
    r = client.post("/agent2/assessments", json=INPUTS[CASE])
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["agent_version"].startswith("a2-agent")
    assert body["meta"]["result_id"].startswith(f"A2-{CASE}-")     # 호출부(API)가 발급
    assert all(e["locator"].startswith("chunk:") for a in body["assessments"] for e in a["evidence"])


def test_each_call_gets_a_new_result_id(client, monkeypatch):
    _use(monkeypatch, ToolAgent2(ScriptLLM(GOLD[CASE])))
    ids = {client.post("/agent2/assessments", json=INPUTS[CASE]).json()["meta"]["result_id"] for _ in range(2)}
    assert len(ids) == 2


def test_unknown_baseline_is_422(client, monkeypatch):
    _use(monkeypatch, ToolAgent2(ScriptLLM(GOLD[CASE])))
    bad = {**INPUTS[CASE], "context": {**INPUTS[CASE]["context"], "baseline_id": "P-COM-101:BL-9"}}
    assert client.post("/agent2/assessments", json=bad).status_code == 422


def test_model_failure_is_502_with_error_code(client, monkeypatch):
    class Down:
        def run(self, inp):
            raise AgentFailure(AgentError(code="MODEL_TIMEOUT", message="x", stage="agent2", retryable=True))
    _use(monkeypatch, Down())
    r = client.post("/agent2/assessments", json=INPUTS[CASE])
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "MODEL_TIMEOUT" and r.json()["detail"]["retryable"] is True


def test_fixture_unknown_case_is_404(client):
    other = {**INPUTS[CASE], "context": {**INPUTS[CASE]["context"], "case_id": "CR-NOPE"}}
    other["items"] = [{**it, "case_id": "CR-NOPE"} for it in INPUTS[CASE]["items"]]
    assert client.post("/agent2/assessments", json=other).status_code == 404


def test_malformed_body_is_422(client):
    assert client.post("/agent2/assessments", json={"context": {}}).status_code == 422
