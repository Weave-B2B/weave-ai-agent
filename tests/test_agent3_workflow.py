"""Approval boundaries, edits and rate replacement over the Agent3 MVP API."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from test_agent3_agent import a3_input, a3_payload  # noqa: F401
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import Agent3Workflow, EditImpact, ReviewImpact, GenerateDraft, WorkflowConflict
from app.api import agent3_routes
from app.core.config import settings
from app.llm.fake import FakeLlm
from app.main import app


@pytest.fixture
def workflow(a3_payload):
    class TestLlm(FakeLlm):
        def _call(self, **kwargs):
            if kwargs["tool_name"] == "submit_negotiation_draft":
                from app.llm.base import RawResponse
                return RawResponse(payload={"narrative": "안녕하세요. 변경 요청의 검토 내용을 전달드립니다.\n\n[[APPROVED_FACTS]]\n\n내용을 검토하신 뒤 함께 협의 부탁드립니다. 감사합니다."})
            return super()._call(**kwargs)
    return Agent3Workflow(LlmAgent3(TestLlm([copy.deepcopy(a3_payload) for _ in range(5)])))


def review_request(view, **changes):
    body = {"state_revision": view["state_revision"], "target_result_id": view["impact"]["meta"]["result_id"],
        "snapshot_hash": view["snapshot_hash"], "request_id": "request-1", "reviewer_ref": "backend-pm-1",
        "approved": True, "acknowledge_unknown_schedule": True}
    body.update(changes)
    return ReviewImpact.model_validate(body)


def generate(workflow, view):
    return workflow.generate_draft(view["session_id"], GenerateDraft(state_revision=view["state_revision"], review_id=view["approval"]["review_id"]))


def edit_request(view, md=4.5):
    edits = [{"item_ids": e["item_ids"], "lines": copy.deepcopy(e["lines"])} for e in view["impact"]["estimates"]]
    edits[0]["lines"][0]["md"] = md
    return EditImpact(state_revision=view["state_revision"], target_result_id=view["impact"]["meta"]["result_id"],
        estimates=edits, reason="실무 리드 공수 검토")


def test_workflow_never_creates_draft_before_pm_approval(workflow, a3_input):
    before = copy.deepcopy(a3_input.model_dump())
    view = workflow.prepare(a3_input)
    assert view["phase"] == "WAITING_IMPACT_APPROVAL"
    assert view["draft"] is None and view["impact"]["draft"] is None
    result = workflow.review(view["session_id"], review_request(view))
    assert result["phase"] == "APPROVED" and result["draft"] is None
    result = generate(workflow, result)
    assert result["phase"] == "DRAFT_READY"
    assert result["draft"]["cost"] == "판매 산정가 5,150,000원 / VAT 별도 / 단가표 RC-SYNTH-2026H2-v1"
    email = result["draft"]["email_body"]
    assert "5,150,000원" in email and "예상 공수" in email
    assert "일정 영향은" in email and "[[APPROVED_FACTS]]" not in email
    assert "RC-SYNTH" not in email and "원가" not in email and "마진" not in email
    assert "변경 요청" in result["draft"]["email_subject"]
    assert "일정 영향 미산정" in result["draft"]["impact"]
    assert "FSP-A-001-v1.0:F-11" in result["draft"]["scope"]
    assert "가능하면" in result["draft"]["scope"]
    assert result["approval"]["review_id"] in result["draft"]["source_result_ids"]
    assert a3_input.model_dump() == before
    assert not result["impact"]["billing_authorized"]


def test_pm_edit_recalculates_and_invalidates_old_approval_and_draft(workflow, a3_input):
    old = workflow.prepare(a3_input)
    approved = workflow.review(old["session_id"], review_request(old))
    edited = workflow.edit(old["session_id"], edit_request(approved))
    assert edited["phase"] == "WAITING_IMPACT_APPROVAL" and edited["draft"] is None
    assert edited["approval"] is None
    assert edited["impact"]["meta"]["result_id"] != old["impact"]["meta"]["result_id"]
    assert edited["impact"]["estimates"][0]["totals"]["price"] == 5850000
    assert edited["history"][0]["approval"]["approved"] is True
    with pytest.raises(WorkflowConflict):
        workflow.review(old["session_id"], review_request(old))
    final = workflow.review(old["session_id"], review_request(edited, request_id="request-2"))
    final = generate(workflow, final)
    assert "5,850,000원" in final["draft"]["cost"]


def test_rejection_and_idempotency_preserve_decisions(workflow, a3_input):
    view = workflow.prepare(a3_input)
    rejected = workflow.review(view["session_id"], review_request(view, approved=False, reason="공수 재검토"))
    assert rejected["draft"] is None and rejected["phase"] == "WAITING_IMPACT_APPROVAL"
    accepted_req = review_request(rejected, request_id="approve-2")
    accepted = workflow.review(view["session_id"], accepted_req)
    assert workflow.review(view["session_id"], accepted_req) == accepted
    assert accepted["history"][0]["approval"]["approved"] is False
    with pytest.raises(WorkflowConflict):
        workflow.review(view["session_id"], accepted_req.model_copy(update={"approved": False}))


@pytest.mark.parametrize("change", ["revision", "result", "hash"])
def test_stale_approval_is_rejected(workflow, a3_input, change):
    view = workflow.prepare(a3_input)
    fields = {"revision": {"state_revision": 10}, "result": {"target_result_id": "old"}, "hash": {"snapshot_hash": "changed"}}
    with pytest.raises(WorkflowConflict):
        workflow.review(view["session_id"], review_request(view, **fields[change]))
    assert workflow.get(view["session_id"])["draft"] is None


def test_unknown_schedule_requires_explicit_acknowledgment(workflow, a3_input):
    view = workflow.prepare(a3_input)
    with pytest.raises(ValueError, match="unestimated schedule"):
        workflow.review(view["session_id"], review_request(view, acknowledge_unknown_schedule=False))


def test_new_estimation_invalidates_previous_session(workflow, a3_input):
    old = workflow.prepare(a3_input)
    workflow.prepare(a3_input)
    with pytest.raises(WorkflowConflict, match="newer impact"):
        workflow.review(old["session_id"], review_request(old))


def test_supplied_rate_file_can_be_replaced_but_old_result_cannot_be_approved(workflow, a3_input, tmp_path, monkeypatch):
    original = settings.data_dir / "kb" / "rate_cards.json"
    path = tmp_path / "company_rates.json"
    path.write_bytes(original.read_bytes())
    monkeypatch.setattr(settings, "rate_card_file", path)
    view = workflow.prepare(a3_input)
    data = json.loads(path.read_text())
    data["rate_cards"][a3_input.rate_card_id]["rates"]["BE"]["price"] = 800000
    path.write_text(json.dumps(data))
    with pytest.raises(WorkflowConflict, match="rate card changed"):
        workflow.review(view["session_id"], review_request(view))
    recalculated = workflow.edit(view["session_id"], edit_request(view, md=2.5))
    assert recalculated["impact"]["estimates"][0]["totals"]["price"] == 4700000
    assert recalculated["rate_card_fingerprint"] != view["rate_card_fingerprint"]


def test_missing_rates_cannot_be_approved(workflow, a3_input):
    a3_input.rate_card_id = "RC-SYNTH-2026H2-noQA"
    view = workflow.prepare(a3_input)
    assert view["phase"] == "WAITING_INPUT"
    with pytest.raises(WorkflowConflict):
        workflow.review(view["session_id"], review_request(view))


def test_edit_can_resolve_missing_rate_by_removing_unused_role(workflow, a3_input):
    a3_input.rate_card_id = "RC-SYNTH-2026H2-noQA"
    view = workflow.prepare(a3_input)
    req = edit_request(view)
    for estimate in req.estimates:
        estimate.lines = [line for line in estimate.lines if line.role != "QA"]
    edited = workflow.edit(view["session_id"], req)
    assert edited["phase"] == "WAITING_IMPACT_APPROVAL"
    assert not any(q["field"] == "rate:QA" for q in edited["impact"]["questions"])


def test_get_returns_an_isolated_copy(workflow, a3_input):
    view = workflow.prepare(a3_input)
    view["impact"]["estimates"][0]["totals"]["price"] = 1
    assert workflow.get(view["session_id"])["impact"]["estimates"][0]["totals"]["price"] == 5150000


def test_changed_effort_rules_block_approval(workflow, a3_input, monkeypatch):
    from app.agents.agent3 import workflow as workflow_module
    from app.agents.agent3.effort import load_effort_rules
    view = workflow.prepare(a3_input)
    changed = load_effort_rules()
    changed["version"] = "changed"
    monkeypatch.setattr(workflow_module, "load_effort_rules", lambda: changed)
    with pytest.raises(WorkflowConflict, match="changed"):
        workflow.review(view["session_id"], review_request(view))


def test_workflow_api_runs_approval_flow_and_rejects_tampering(workflow, a3_input, monkeypatch):
    monkeypatch.setattr(agent3_routes, "get_workflow", lambda: workflow)
    client = TestClient(app)
    response = client.post("/agent3/impacts", json=a3_input.model_dump(mode="json"))
    assert response.status_code == 200
    view = response.json()
    base = f"/agent3/impacts/{view['session_id']}"
    assert client.get(base).json()["draft"] is None
    req = review_request(view).model_dump(mode="json")
    assert client.post(base + "/review", json={**req, "price": 1}).status_code == 422
    assert client.post(base + "/review", json={**req, "snapshot_hash": "old"}).status_code == 409
    response = client.post(base + "/review", json=req)
    assert response.status_code == 200 and response.json()["phase"] == "APPROVED"
    assert client.post(base + "/review", json=req).json()["draft_result_id"] is None
    approved = response.json()
    response = client.post(base + "/draft", json={"state_revision": approved["state_revision"], "review_id": approved["approval"]["review_id"]})
    assert response.status_code == 200 and response.json()["phase"] == "DRAFT_READY"
    assert client.get("/agent3/impacts/missing").status_code == 404
    assert client.post(base + "/edit", json={**edit_request(response.json()).model_dump(), "totals": {"price": 1}}).status_code == 422


def test_draft_failure_preserves_approval_and_retries(workflow, a3_input, monkeypatch):
    view = workflow.prepare(a3_input)
    with pytest.raises(WorkflowConflict):
        workflow.generate_draft(view["session_id"], GenerateDraft(state_revision=view["state_revision"], review_id="none"))
    approved = workflow.review(view["session_id"], review_request(view))
    original = workflow.agent.llm.complete_structured
    def bad(**kwargs):
        from app.llm.base import LlmResult, Usage
        from app.agents.agent3.workflow import DraftNarrative
        return LlmResult(parsed=DraftNarrative(narrative="[[APPROVED_FACTS]]\n비용은 1원으로 확정됩니다."), usage=Usage(), latency_ms=0, provider="fake", model_id="bad", retried=False)
    monkeypatch.setattr(workflow.agent.llm, "complete_structured", bad)
    with pytest.raises(ValueError, match="검증"):
        generate(workflow, approved)
    assert workflow.get(view["session_id"])["approval"] == approved["approval"]
    assert workflow.get(view["session_id"])["draft"] is None
    monkeypatch.setattr(workflow.agent.llm, "complete_structured", original)
    final = generate(workflow, approved)
    assert final["draft_model"].startswith("fake:")
    edited = workflow.edit(view["session_id"], edit_request(final))
    assert edited["approval"] is None and edited["draft"] is None
    with pytest.raises(WorkflowConflict):
        generate(workflow, approved)


def test_work_scope_edit_recalculates_and_clears_approval(workflow, a3_input):
    old = workflow.prepare(a3_input)
    approved = workflow.review(old['session_id'], review_request(old))
    edit = edit_request(approved)
    edit.estimates[0].lines[0].task = '변경된 작업 범위'
    edit.estimates[0].lines[0].role = 'FE'
    edited = workflow.edit(old['session_id'], edit)
    assert edited['impact']['estimates'][0]['lines'][0]['task'] == '변경된 작업 범위'
    assert edited['impact']['estimates'][0]['lines'][0]['role'] == 'FE'
    assert edited['impact']['estimates'][0]['totals']['price'] != approved['impact']['estimates'][0]['totals']['price']
    assert edited['approval'] is None and edited['draft'] is None
    empty = edit_request(edited)
    empty.estimates[0].lines[0].task = ' '
    with pytest.raises(ValueError, match='task description'):
        workflow.edit(old['session_id'], empty)


def test_new_rate_card_reprices_preserving_edited_lines(workflow, a3_input, tmp_path, monkeypatch):
    data = json.loads((settings.data_dir / 'kb' / 'rate_cards.json').read_text())
    source = a3_input.rate_card_id
    data['rate_cards']['TEST-UPDATED'] = copy.deepcopy(data['rate_cards'][source])
    for rate in data['rate_cards']['TEST-UPDATED']['rates'].values():
        if rate.get('price') is not None:
            rate['price'] += 100000
    path = tmp_path / 'rates.json'
    path.write_text(json.dumps(data))
    monkeypatch.setattr(settings, 'rate_card_file', path)
    view = workflow.prepare(a3_input)
    request = edit_request(view)
    request.estimates[0].lines[0].task = 'PM이 수정한 작업'
    view = workflow.edit(view['session_id'], request)
    approved = workflow.review(view['session_id'], review_request(view))
    lines = copy.deepcopy(approved['impact']['estimates'][0]['lines'])
    request = edit_request(approved, md=lines[0]['md'])
    request.rate_card_id = 'TEST-UPDATED'
    updated = workflow.edit(view['session_id'], request)
    estimate = updated['impact']['estimates'][0]
    assert estimate['lines'] == lines and estimate['rate_card_id'] == 'TEST-UPDATED'
    assert estimate['totals']['price'] > approved['impact']['estimates'][0]['totals']['price']
    assert updated['approval'] is None and updated['history'][-1]['approval']['approved']
