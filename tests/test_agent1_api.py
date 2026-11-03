"""POST /agent1/items: fixture mode, LLM mode, and API error mapping."""
import pytest
from fastapi.testclient import TestClient

from app.agents.agent1.agent import LlmAgent1
from app.agents.base import AgentFailure
from app.api import agent1_routes
from app.core.config import settings
from app.llm.fake import FakeLlm
from app.main import app
from schema.schemas import AgentError


INPUT = {
    "context": {
        "project_id": "P-TEST",
        "case_id": "CASE-TEST",
        "request_revision": 1,
        "analysis_id": "ANALYSIS-TEST",
    },
    "received_at": "2026-10-28T10:00:00+09:00",
    "sender_role": "고객",
    "subject": "새 기능",
    "message_text": "고객 원문 비밀 요청입니다.",
}

DRAFT = {
    "items": [{
        "item_type": "function",
        "intent": "change_request",
        "text": "새 기능을 추가합니다.",
        "quotes": ["고객 원문 비밀 요청입니다."],
        "inferred": False,
    }],
    "questions": [],
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "fixture")
    monkeypatch.setattr(settings, "agent1_mode", "fixture")
    cached = agent1_routes.get_agent1
    cached.cache_clear()
    yield TestClient(app)
    cached.cache_clear()


def _use(monkeypatch, agent):
    monkeypatch.setattr(agent1_routes, "get_agent1", lambda: agent)


def test_fixture_mode_uses_case_gold_and_issues_ids(client, monkeypatch):
    from app.agents.fixture import FixtureAgent1, load_chain
    _use(monkeypatch, FixtureAgent1(load_chain()))

    inp = {
        **INPUT,
        "context": {**INPUT["context"], "case_id": "CR-A-001"},
    }
    response = client.post("/agent1/items", json=inp)

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["case_id"] == "CR-A-001"
    assert body["meta"]["result_id"].startswith("A1-CR-A-001-")
    assert body["meta"]["created_at"]


def test_llm_mode_runs_agent1(client, monkeypatch):
    agent = LlmAgent1(FakeLlm([DRAFT]), feature_search=lambda _text, _baseline: [])
    _use(monkeypatch, agent)

    response = client.post("/agent1/items", json=INPUT)

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["text"] == "새 기능을 추가합니다."
    assert body["items"][0]["spans"][0]["text"] == INPUT["message_text"]
    assert body["meta"]["model"] == "fake:fake"
    assert body["meta"]["result_id"]


def test_each_llm_call_gets_new_result_id(client, monkeypatch):
    agent = LlmAgent1(FakeLlm([DRAFT, DRAFT]), feature_search=lambda _text, _baseline: [])
    _use(monkeypatch, agent)

    ids = {client.post("/agent1/items", json=INPUT).json()["meta"]["result_id"] for _ in range(2)}
    assert len(ids) == 2


def test_model_failure_maps_to_502(client, monkeypatch):
    class Down:
        def run(self, inp):
            raise AgentFailure(AgentError(code="MODEL_TIMEOUT", message="x", stage="agent1", retryable=True))

    _use(monkeypatch, Down())
    response = client.post("/agent1/items", json=INPUT)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "MODEL_TIMEOUT"
    assert response.json()["detail"]["retryable"] is True


def test_malformed_body_is_422(client):
    assert client.post("/agent1/items", json={"context": {}}).status_code == 422


def test_unknown_fixture_case_is_404(client, monkeypatch):
    from app.agents.fixture import FixtureAgent1, load_chain
    _use(monkeypatch, FixtureAgent1(load_chain()))

    inp = {**INPUT, "context": {**INPUT["context"], "case_id": "CR-NOPE"}}
    assert client.post("/agent1/items", json=inp).status_code == 404
