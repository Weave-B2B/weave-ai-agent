"""Persistent local screen bridge and the real common Run runtime (offline models)."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from test_run_supervisor import setup
from test_agent3_agent import a3_payload
from app.api import local_run_routes as routes
from app.main import app
from app.orchestration.local_run_store import LocalRunStore
from app.orchestration.run_coordination import AcquireExecution, ReserveLlmAttempt
from app.orchestration.run_state import InvalidStateTransition, StateRevisionConflict


@pytest.fixture
def local(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOTYPE_LAB", "1")
    store = LocalRunStore(tmp_path / "runs.sqlite3")
    workers, llm, draft = setup[1:]
    monkeypatch.setattr(routes, "_workers", lambda provider: dict(agent1=workers.agent1, agent2=workers.agent2,
        agent3=workers.agent3, llm=llm, draft_llm=draft))
    routes.provider_workers.cache_clear()
    app.dependency_overrides[routes.get_store] = lambda: store
    routes._progress.clear()
    with TestClient(app) as client:
        yield client, store, setup
    app.dependency_overrides.pop(routes.get_store, None)
    routes._progress.clear()
    routes.provider_workers.cache_clear()


def create(local):
    client, store, setup = local
    inputs = setup[0].get_inputs("run-1")
    response = client.post("/prototype/runs/cases", json={"input": inputs.agent1_input.model_dump(mode="json"),
        "rate_card_id": inputs.rate_card_id, "provider": "gpt"})
    assert response.status_code == 200, response.text
    return response.json()


def finish(local):
    current = create(local)
    for _ in range(8):
        response = local[0].post("/prototype/runs/cases/CR-A-001/step", json={"state_revision": current["state_revision"]})
        assert response.status_code == 200, response.text
        current = response.json()["state"]
        if current["ready_for_review"]:
            return current
    pytest.fail("Expected final review")


def test_local_full_flow_persists_and_restores_without_sessions(local):
    current = finish(local)
    client, store, setup = local
    assert current["backend_connected"] is False and current["storage"] == "local-sqlite"
    assert current["agent3"]["documents"]["email"]["body"]
    assert current["agent3"]["documents"]["agreement"]["body"]
    assert current["llm_calls"] >= 6
    restored = LocalRunStore(store.path).get_coordinated_run("CR-A-001")
    assert restored.state.state_revision == current["state_revision"]
    routes._progress.clear()
    fetched = client.get("/prototype/runs/cases/CR-A-001").json()
    assert fetched["agent3"]["documents"] == current["agent3"]["documents"]
    assert fetched["progress"] and "execution_token" not in str(fetched)
    body = dict(state_revision=current["state_revision"], expected_snapshot_hash=current["agent3"]["snapshot_hash"],
        decision="approve", operation_id="review", acknowledge_unknown_schedule=True)
    approved = client.post("/prototype/runs/cases/CR-A-001/review", json=body)
    assert approved.status_code == 200 and approved.json()["review"]["decision"] == "approve"
    assert client.post("/prototype/runs/cases/CR-A-001/review", json=body).status_code == 200
    assert LocalRunStore(store.path).get_state("CR-A-001").status == "COMPLETED"


def test_final_review_requires_unknown_schedule_acknowledgement(local):
    current = finish(local)
    response = local[0].post("/prototype/runs/cases/CR-A-001/review", json={"state_revision": current["state_revision"],
        "expected_snapshot_hash": current["agent3"]["snapshot_hash"], "decision": "approve", "operation_id": "review"})
    assert response.status_code == 422
    assert local[1].get_state("CR-A-001").status == "WAITING_PM_REVIEW"


def test_other_site_cannot_invoke_paid_local_model(local):
    response = local[0].post("/prototype/runs/cases", headers={"Origin": "https://other-site.example"}, json={})
    assert response.status_code == 403


def test_routes_disabled_outside_explicit_prototype_mode(local, monkeypatch):
    monkeypatch.delenv("PROTOTYPE_LAB")
    assert local[0].get("/prototype/runs/cases/unknown").status_code == 404


def test_independent_store_instances_enforce_compare_and_swap(local):
    current = create(local)
    a, b = LocalRunStore(local[1].path), LocalRunStore(local[1].path)
    def acquire(store):
        request = AcquireExecution(run_id="CR-A-001", agent_type="agent2", expected_state_revision=current["state_revision"],
            operation_id=f"acquire-{id(store)}")
        try:
            store.acquire_execution(request)
            return True
        except (StateRevisionConflict, InvalidStateTransition):
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(acquire, [a, b])) == 1


def test_provider_attempt_limit_is_reserved_before_call(local):
    current = create(local)
    store = local[1]
    calls_before = len(local[2][2].calls)
    while store.get_coordinated_run("CR-A-001").coordination.segment_llm_calls < 40:
        state = store.get_state("CR-A-001")
        store.reserve_llm_attempt(ReserveLlmAttempt(run_id="CR-A-001", expected_state_revision=state.state_revision,
            operation_id=f"reserve-{state.state_revision}"))
    state = store.get_state("CR-A-001")
    response = local[0].post("/prototype/runs/cases/CR-A-001/step", json={"state_revision": state.state_revision})
    assert response.status_code == 200 and response.json()["event"]["status"] == "waiting"
    assert len(local[2][2].calls) == calls_before
    assert LocalRunStore(store.path).get_coordinated_run("CR-A-001").coordination.segment_llm_calls == 40
