"""Run API를 실제 Backend(HttpRunStateClient)에 연결해 검증한다. test_run_state_contract.py와 같은 환경변수가 있을 때만 실행된다."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from schema.schemas import Agent1Input, PMReview

from app.agents.fixture import load_chain
from app.core.config import settings
from app.main import app

ENV = ("WEAVE_BACKEND_URL", "WEAVE_INTERNAL_TOKEN", "WEAVE_ACTOR_MEMBER_ID", "WEAVE_PROJECT_ID")
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ENV), reason="실제 Backend 환경변수가 없어 건너뜀")

RC, ROW = "RC-SYNTH-2026H2-v1", load_chain()["CR-A-001"]


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "backend_base_url", os.environ["WEAVE_BACKEND_URL"])
    monkeypatch.setattr(settings, "backend_internal_token", os.environ["WEAVE_INTERNAL_TOKEN"])
    monkeypatch.setattr(settings, "agent_mode", "fixture")
    from app.orchestration import services
    services._agents.cache_clear()
    return TestClient(app), {"X-Actor-Member-Id": os.environ["WEAVE_ACTOR_MEMBER_ID"]}


def test_full_flow_through_api_and_real_backend(api):
    c, h = api
    created = c.post("/api/v2/runs", headers=h, json={
        "project_id": os.environ["WEAVE_PROJECT_ID"], "change_request_id": str(uuid.uuid4()), "rate_card_id": RC,
        "agent1_input": Agent1Input.model_validate(ROW["agent1_input"]).model_dump(mode="json")})
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    first = c.post(f"/api/v2/runs/{run_id}/orchestrations", headers=h).json()
    assert (first["outcome"], first["pending_review"]["agent_type"]) == ("WAITING_FOR_REVIEW", "agent2")

    state = c.get(f"/api/v2/runs/{run_id}", headers=h).json()
    review = PMReview.model_validate({**ROW["pm_review"], "target_result_id": state["agents"]["agent2"]["current_result_id"]})
    body = {"expected_state_revision": state["state_revision"], "operation_id": f"rv-{uuid.uuid4().hex[:8]}",
            "payload": review.model_dump(mode="json")}
    assert c.post(f"/api/v2/runs/{run_id}/pm-review", headers=h, json=body).json()["replayed"] is False
    assert c.post(f"/api/v2/runs/{run_id}/pm-review", headers=h, json=body).json()["replayed"] is True

    done = c.post(f"/api/v2/runs/{run_id}/resume", headers=h, json={"decision": "APPROVED"}).json()
    assert (done["outcome"], done["run_status"]) == ("COMPLETED", "COMPLETED")


def test_backend_errors_are_mapped(api):
    c, h = api
    assert c.get(f"/api/v2/runs/{uuid.uuid4()}", headers=h).status_code == 404


def test_direct_run_edit_and_rerun_through_real_backend(api):
    c, h = api
    run_id = c.post("/api/v2/runs", headers=h, json={
        "project_id": os.environ["WEAVE_PROJECT_ID"], "change_request_id": str(uuid.uuid4()), "rate_card_id": RC,
        "agent1_input": Agent1Input.model_validate(ROW["agent1_input"]).model_dump(mode="json")}).json()["run_id"]
    base = f"/api/v2/runs/{run_id}"
    assert c.post(f"{base}/agents/agent2/executions", headers=h).json()["stop_reason"] == "START_REJECTED"
    assert c.post(f"{base}/agents/agent1/executions", headers=h).json()["outcome"] == "COMPLETED"
    assert c.post(f"{base}/agents/agent2/executions", headers=h).json()["outcome"] == "COMPLETED"

    state = c.get(base, headers=h).json()
    old = state["agents"]["agent1"]["current_result_id"]
    payload = c.get(f"{base}/results/{old}", headers=h).json()["result_payload"]
    edit = {"expected_state_revision": state["state_revision"], "operation_id": f"ed-{uuid.uuid4().hex[:8]}",
            "target_result_id": old, "edited_payload": payload, "reason": "보정"}
    receipt = c.post(f"{base}/agents/agent1/edits", headers=h, json=edit).json()
    assert receipt["result_id"] != old and receipt["replayed"] is False and receipt["result_revision"] == 2
    assert c.post(f"{base}/agents/agent1/edits", headers=h, json=edit).json()["replayed"] is True
    agents = c.get(base, headers=h).json()["agents"]
    assert agents["agent1"]["current_result_id"] == receipt["result_id"] and agents["agent2"]["stale"] is True

    assert c.post(f"{base}/agents/agent2/executions", headers=h).json()["outcome"] == "COMPLETED"
    assert c.get(base, headers=h).json()["agents"]["agent2"]["stale"] is False
    assert c.get(f"{base}/results/{old}", headers=h).json()["result_revision"] == 1      # 과거 결과는 불변
    assert c.post(f"{base}/agents/agent1/edits", headers=h, json={**edit, "operation_id": "x"}).status_code == 409
