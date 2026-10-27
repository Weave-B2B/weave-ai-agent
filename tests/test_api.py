import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app

RC = "RC-SYNTH-2026H2-v1"


@pytest.fixture
def client():
    routes.get_engine.cache_clear()
    return TestClient(app)


def test_full_flow_over_http(client, chain):
    row = next(r for r in chain if r["agent3_gold"]["status"] == "IMPACT_DRAFT_READY")
    cid = row["case_id"]
    r = client.post(f"/cases/{cid}/run", json={"input": row["agent1_input"], "rate_card_id": RC})
    assert r.status_code == 200 and r.json()["state"]["stage"] == "pm_items"
    v = r.json()
    r = client.post(f"/cases/{cid}/resume", json={"kind": "items", "state_revision": v["state"]["state_revision"], "items": v["a1"]["items"]})
    v = r.json(); assert v["state"]["stage"] == "pm_review"
    review = {**row["pm_review"], "target_result_id": v["a2"]["meta"]["result_id"]}
    r = client.post(f"/cases/{cid}/resume", json={"kind": "pm_review", "state_revision": v["state"]["state_revision"], "review": review})
    v = r.json(); assert v["state"]["stage"] == "lead_review" and v["a3"]["billing_authorized"] is False
    r = client.post(f"/cases/{cid}/resume", json={"kind": "lead", "state_revision": v["state"]["state_revision"], "approved": True})
    assert r.json()["state"]["status"] == "COMPLETED"
    assert client.get(f"/cases/{cid}/state").json()["state"]["status"] == "COMPLETED"


def test_conflicts_and_validation_errors(client, chain):
    row = chain[0]; cid = row["case_id"]
    run = client.post(f"/cases/{cid}/run", json={"input": row["agent1_input"], "rate_card_id": RC}).json()
    assert client.post("/cases/OTHER/run", json={"input": row["agent1_input"], "rate_card_id": RC}).status_code == 422
    stale = client.post(f"/cases/{cid}/resume", json={"kind": "items", "state_revision": 0, "items": run["a1"]["items"]})
    assert stale.status_code == 409
    early = client.post(f"/cases/{cid}/resume", json={"kind": "lead", "state_revision": run["state"]["state_revision"], "approved": True})
    assert early.status_code == 409
    assert client.get("/cases/NOPE/state").status_code == 404
    extra = client.post(f"/cases/{cid}/resume", json={"kind": "lead", "state_revision": 1, "approved": True, "x": 1})
    assert extra.status_code == 422                                  # 공유 스키마처럼 정의되지 않은 필드는 거부
