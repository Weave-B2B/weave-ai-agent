"""Run 오케스트레이션 API: Fake RunStateClient + fixture Agent로 라우트·오류 매핑을 검증한다.
통과는 실제 Backend 연동 검증이 아니다(Backend 연동은 test_run_state_contract.py의 live 변형)."""
import uuid

import pytest
from fastapi.testclient import TestClient
from schema.schemas import Agent1Input, PMReview

from app.agents.base import AgentSet
from app.agents.fixture import FixtureAgent1, FixtureAgent2, FixtureAgent3, load_chain
from app.api.run_routes import get_services
from app.core.config import settings
from app.main import app
from app.orchestration.fake_run_state import FakeRunStateClient
from app.orchestration.runtime import BackendRunInputProvider, OrchestrationRuntime
from app.orchestration.services import RunServices

CASE, RC, ACTOR = "CR-A-001", "RC-SYNTH-2026H2-v1", str(uuid.UUID(int=0xB1))
ROW = load_chain()[CASE]
H = {"X-Actor-Member-Id": ACTOR}


class FakeBackend:
    def __init__(self, fake):
        self.fake = fake
        self.get_state, self.submit_input, self.get_result = fake.get_state, fake.submit_input, fake.get_result

    def create_run(self, project_id, change_request_id, inputs=None):
        run_id = f"run-{change_request_id}"
        self.fake.create_run(run_id, inputs)
        return self.fake.get_state(run_id)


@pytest.fixture
def client():
    fake, chain = FakeRunStateClient(strict_commit=True), load_chain()
    rt = OrchestrationRuntime(fake, AgentSet(FixtureAgent1(chain), FixtureAgent2(chain), FixtureAgent3(chain)),
                              BackendRunInputProvider(fake))
    app.dependency_overrides[get_services] = lambda: RunServices(FakeBackend(fake), rt)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create(c, cr="1"):
    r = c.post("/api/v2/runs", headers=H, json={
        "project_id": "p1", "change_request_id": cr, "rate_card_id": RC,
        "agent1_input": Agent1Input.model_validate(ROW["agent1_input"]).model_dump(mode="json")})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_then_get(client):
    created = _create(client)
    assert {a["status"] for a in created["agents"].values()} == {"NOT_STARTED"}
    assert client.get(f"/api/v2/runs/{created['run_id']}", headers=H).json() == created


def test_orchestrate_waits_for_review_then_pm_review_and_resume_complete(client):
    run_id = _create(client)["run_id"]
    first = client.post(f"/api/v2/runs/{run_id}/orchestrations", headers=H).json()
    assert first["outcome"] == "WAITING_FOR_REVIEW" and first["pending_review"]["agent_type"] == "agent2"

    state = client.get(f"/api/v2/runs/{run_id}", headers=H).json()
    review = PMReview.model_validate({**ROW["pm_review"], "target_result_id": state["agents"]["agent2"]["current_result_id"]})
    body = {"expected_state_revision": state["state_revision"], "operation_id": "op-rv", "payload": review.model_dump(mode="json")}
    r = client.post(f"/api/v2/runs/{run_id}/pm-review", headers=H, json=body)
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert client.post(f"/api/v2/runs/{run_id}/pm-review", headers=H, json=body).json()["replayed"] is True

    done = client.post(f"/api/v2/runs/{run_id}/resume", headers=H, json={"decision": "APPROVED"}).json()
    assert (done["outcome"], done["run_status"]) == ("COMPLETED", "COMPLETED")


def test_error_mapping(client):
    assert client.get("/api/v2/runs/nope", headers=H).status_code == 404
    assert client.get("/api/v2/runs/nope").status_code == 422            # 행위자 헤더 필수
    run_id = _create(client)["run_id"]
    r = client.post(f"/api/v2/runs/{run_id}/pm-review", headers=H, json={
        "expected_state_revision": 99, "operation_id": "o", "payload": {**ROW["pm_review"], "target_result_id": None}})
    assert r.status_code == 409 and r.json()["code"] == "STATE_REVISION_CONFLICT"
    assert r.json()["actual_state_revision"] == 0
    assert client.post("/api/v2/runs", headers=H, json={"project_id": "p"}).status_code == 422


def test_backend_not_configured_returns_503(monkeypatch):
    monkeypatch.setattr(settings, "backend_base_url", "")
    r = TestClient(app).get("/api/v2/runs/x", headers=H)
    assert r.status_code == 503 and r.json()["code"] == "BACKEND_NOT_CONFIGURED"


def _agent_status(c, run_id):
    return {k: (v["status"], v["stale"]) for k, v in c.get(f"/api/v2/runs/{run_id}", headers=H).json()["agents"].items()}


def test_direct_run_single_agent_edit_and_rerun(client):
    run_id = _create(client)["run_id"]
    base = f"/api/v2/runs/{run_id}"
    r = client.post(f"{base}/agents/agent2/executions", headers=H).json()
    assert (r["outcome"], r["stop_reason"]) == ("STOPPED", "START_REJECTED")        # 선행 A1 없음

    a1 = client.post(f"{base}/agents/agent1/executions", headers=H).json()
    assert a1["outcome"] == "COMPLETED" and [e["agent_type"] for e in a1["executed"]] == ["agent1"]
    assert _agent_status(client, run_id)["agent2"] == ("NOT_STARTED", False)          # 이어서 실행하지 않는다

    assert client.post(f"{base}/agents/agent2/executions", headers=H).json()["outcome"] == "COMPLETED"
    state = client.get(base, headers=H).json()
    a3 = client.post(f"{base}/agents/agent3/executions", headers=H).json()
    assert (a3["outcome"], a3["stop_reason"]) == ("STOPPED", "PREFLIGHT_BLOCKED")    # PM 검토 전
    assert client.get(base, headers=H).json()["state_revision"] == state["state_revision"]   # 대기를 만들지 않는다

    old = state["agents"]["agent1"]["current_result_id"]
    payload = client.get(f"{base}/results/{old}", headers=H).json()["result_payload"]
    edit = {"expected_state_revision": state["state_revision"], "operation_id": "op-edit", "target_result_id": old, "reason": "항목 보정",
            "edited_payload": payload}
    receipt = client.post(f"{base}/agents/agent1/edits", headers=H, json=edit).json()
    assert receipt["result_id"] != old and receipt["replayed"] is False
    assert client.post(f"{base}/agents/agent1/edits", headers=H, json=edit).json()["replayed"] is True
    assert client.get(base, headers=H).json()["agents"]["agent1"]["current_result_id"] == receipt["result_id"]
    assert _agent_status(client, run_id)["agent2"] == ("COMPLETED", True)            # 하위 stale

    assert client.post(f"{base}/agents/agent3/executions", headers=H).json()["stop_reason"] == "START_REJECTED"
    re2 = client.post(f"{base}/agents/agent2/executions", headers=H).json()
    assert re2["outcome"] == "COMPLETED"
    assert _agent_status(client, run_id)["agent2"] == ("COMPLETED", False)


def test_edit_errors(client):
    run_id = _create(client)["run_id"]
    base = f"/api/v2/runs/{run_id}"
    body = {"expected_state_revision": 0, "operation_id": "o", "target_result_id": "x", "edited_payload": {}}
    client.post(f"{base}/agents/agent1/executions", headers=H)
    r = client.post(f"{base}/agents/agent1/edits", headers=H, json={**body, "expected_state_revision": 99})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_EDIT_PAYLOAD"        # 스키마 불일치
    assert client.post(f"{base}/agents/agent9/executions", headers=H).status_code == 422
    cur = client.get(base, headers=H).json()["agents"]["agent1"]["current_result_id"]
    payload = client.get(f"{base}/results/{cur}", headers=H).json()["result_payload"]
    rev = client.get(base, headers=H).json()["state_revision"]
    stale = {**body, "expected_state_revision": rev, "target_result_id": "old-id", "edited_payload": payload}
    assert client.post(f"{base}/agents/agent1/edits", headers=H, json=stale).status_code == 409   # 현재 결과가 아님


def _leaks(o):
    """운영 payload에 남으면 안 되는 평가 전용·debug 값을 찾는다."""
    found = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("gold_origin", "gold_ref") and v is not None:
                found.append(k)
            if k == "debug" and v:
                found.append(k)
            found += _leaks(v)
    elif isinstance(o, list):
        for v in o:
            found += _leaks(v)
    return found


def test_committed_payloads_carry_no_eval_only_or_debug_fields(client):
    run_id = _create(client)["run_id"]
    base = f"/api/v2/runs/{run_id}"
    client.post(f"{base}/orchestrations", headers=H)
    state = client.get(base, headers=H).json()
    ids = [a["current_result_id"] for a in state["agents"].values() if a["current_result_id"]]
    assert len(ids) == 2
    for rid in ids:
        assert _leaks(client.get(f"{base}/results/{rid}", headers=H).json()["result_payload"]) == []

    a1 = client.get(f"{base}/results/{state['agents']['agent1']['current_result_id']}", headers=H).json()["result_payload"]
    a1["meta"]["debug"] = {"x": 1}
    a1["items"][0]["gold_origin"] = "eval-only"
    receipt = client.post(f"{base}/agents/agent1/edits", headers=H, json={
        "expected_state_revision": state["state_revision"], "operation_id": "e1",
        "target_result_id": state["agents"]["agent1"]["current_result_id"], "edited_payload": a1}).json()
    assert _leaks(client.get(f"{base}/results/{receipt['result_id']}", headers=H).json()["result_payload"]) == []
