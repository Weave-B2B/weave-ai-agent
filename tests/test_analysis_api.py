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


def test_request_analysis_tab_connects_custom_case_workflow(client):
    inp = {
        "context": {
            "project_id": "P-COM-101",
            "case_id": "CR-UI-TEST-001",
            "request_revision": 1,
            "baseline_id": "P-COM-101:BL-2",
            "baseline_as_of": "2026-10-05",
            "analysis_id": "UI-CR-UI-TEST-001",
        },
        "received_at": "2026-11-05T15:40:00+09:00",
        "sender_role": "PM",
        "sender_name": "프로토타입",
        "channel": "manual",
        "channel_src": "PROTOTYPE",
        "subject": "요청분석",
        "message_text": "상품 상세페이지에 별점 기능을 추가해 주세요.",
        "answers": [],
    }

    created = client.post("/api/v1/analyses", json={"input": inp, "rate_card_id": RC})
    assert created.status_code == 200
    body = created.json()
    assert body["structured_request"]["structured"]["status"] == "READY"
    assert body["structured_request"]["structured"]["items"]
    assert body["workflow"]["stage"] == "pm_items"

    items = body["structured_request"]["structured"]["items"]
    confirmed = client.post(
        f"/cases/{body['case_id']}/resume",
        json={"kind": "items", "state_revision": body["workflow"]["state_revision"], "items": items},
    )
    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["a2"]["status"] == "READY_FOR_REVIEW"

    assessment = confirmed_body["a2"]["assessments"][0]
    reviewed = client.post(
        f"/cases/{body['case_id']}/resume",
        json={
            "kind": "pm_review",
            "state_revision": confirmed_body["state"]["state_revision"],
            "review": {
                "review_id": "PMR-UI-TEST-001",
                "review_revision": 1,
                "target_result_id": confirmed_body["a2"]["meta"]["result_id"],
                "reviewer": "prototype-pm",
                "reviewed_at": "2026-11-05T15:45:00+09:00",
                "status": "REVIEWED",
                "items": [{
                    "item_id": assessment["item_id"],
                    "ai_label": assessment["label"],
                    "pm_label": assessment["label"],
                    "estimate": False,
                    "reason": "",
                    "evidence_refs": [],
                }],
            },
        },
    )
    assert reviewed.status_code == 200
