"""Run-scoped execution envelope for Agent 1 and Agent 2."""
import pytest
from fastapi.testclient import TestClient

from app.api import agent1_routes, agent2_routes
from app.core.config import settings
from app.main import app
from test_agent2 import INPUTS


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "fixture")
    monkeypatch.setattr(settings, "agent1_mode", "fixture")
    monkeypatch.setattr(settings, "agent2_mode", "fixture")
    agent1_routes.get_agent1.cache_clear()
    agent2_routes.get_agent2.cache_clear()
    yield TestClient(app)
    agent1_routes.get_agent1.cache_clear()
    agent2_routes.get_agent2.cache_clear()


def test_agent1_execution_returns_backend_run_and_revision(client, chain):
    row = next(r for r in chain if r["agent1_input"]["context"]["case_id"] == "CR-A-001")
    response = client.post(
        "/api/v2/runs/run-123/agent1/items",
        json={
            "expected_state_revision": 4,
            "trigger": "manual",
            "input": row["agent1_input"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-123"
    assert body["based_on_state_revision"] == 4
    assert body["trigger"] == "manual"
    assert body["stage"] == "agent1"
    assert body["output"]["meta"]["result_id"]
    assert body["output"]["items"]


def test_agent2_execution_preserves_upstream_result_reference(client):
    inp = INPUTS["CR-A-001"]
    response = client.post(
        "/api/v2/runs/run-456/agent2/assessments",
        json={
            "expected_state_revision": 7,
            "trigger": "orchestrator",
            "input": {
                **inp,
                "a1_result_id": "a1-result-123",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-456"
    assert body["based_on_state_revision"] == 7
    assert body["stage"] == "agent2"
    upstream = body["output"]["meta"]["upstream_result_ids"]
    assert "a1-result-123" in upstream
    assert len(upstream) == len(set(upstream))


def test_execution_request_rejects_unknown_fields_and_negative_revision(client, chain):
    row = next(r for r in chain if r["agent1_input"]["context"]["case_id"] == "CR-A-001")
    base = {
        "expected_state_revision": 0,
        "trigger": "manual",
        "input": row["agent1_input"],
    }

    assert client.post(
        "/api/v2/runs/run-123/agent1/items",
        json={**base, "unexpected": True},
    ).status_code == 422
    assert client.post(
        "/api/v2/runs/run-123/agent1/items",
        json={**base, "expected_state_revision": -1},
    ).status_code == 422
