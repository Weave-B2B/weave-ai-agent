import pytest
from fastapi.testclient import TestClient

from app.api import analysis_routes, routes
from app.main import app


RC = "RC-SYNTH-2026H2-v1"


@pytest.fixture
def client():
    routes.get_engine.cache_clear()
    analysis_routes.get_analysis_service.cache_clear()
    yield TestClient(app)
    routes.get_engine.cache_clear()
    analysis_routes.get_analysis_service.cache_clear()


def test_analysis_create_returns_accumulated_a1_dto(client, chain):
    row = next(r for r in chain if r["agent3_gold"]["status"] == "IMPACT_DRAFT_READY")
    inp = row["agent1_input"]

    response = client.post("/api/v1/analyses", json={"input": inp, "rate_card_id": RC})

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"]
    assert body["case_id"] == inp["context"]["case_id"]
    assert body["contract_request"]["input"]["message_text"] == inp["message_text"]
    assert body["structured_request"]["structured"]["status"] == "READY"
    assert body["scope_analysis"] is None
    assert body["workflow"]["stage"] == "pm_items"


def test_analysis_status_and_get_through_pm_review(client, chain):
    row = next(r for r in chain if r["agent3_gold"]["status"] == "IMPACT_DRAFT_READY")
    inp = row["agent1_input"]
    created = client.post("/api/v1/analyses", json={"input": inp, "rate_card_id": RC}).json()
    analysis_id = created["analysis_id"]
    case_id = created["case_id"]

    status = client.get(f"/api/v1/analyses/{analysis_id}/status")
    assert status.status_code == 200
    assert status.json()["stage"] == "pm_items"
    assert status.json()["state_revision"] == created["workflow"]["state_revision"]

    items = created["structured_request"]["structured"]["items"]
    revision = created["workflow"]["state_revision"]
    confirmed = client.post(
        f"/cases/{case_id}/resume",
        json={"kind": "items", "state_revision": revision, "items": items},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"]["stage"] == "pm_review"

    result = client.get(f"/api/v1/analyses/{analysis_id}")
    assert result.status_code == 200
    body = result.json()
    assert body["scope_analysis"]["scope"]["status"] == "READY_FOR_REVIEW"
    assert body["scope_analysis"]["scope"]["assessments"]
    assert body["cost_estimation"] is None


def test_analysis_not_found_and_malformed_request(client):
    assert client.get("/api/v1/analyses/analysis-missing/status").status_code == 404
    assert client.post("/api/v1/analyses", json={"input": {}, "rate_card_id": RC}).status_code == 422
