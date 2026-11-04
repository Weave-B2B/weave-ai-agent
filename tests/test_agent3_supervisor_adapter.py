"""Host-port contract tests. No shared engine changes or external model calls."""
import copy

import pytest
from pydantic import ValidationError

from test_agent3_agent import a3_input, a3_payload
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.orchestration import Agent3Orchestration
from app.agents.agent3.supervisor_adapter import (
    Agent3CaseSnapshot, Agent3SupervisorAdapter, tool_schemas,
)
from app.agents.agent3.workflow import FAKE_EMAIL, WorkflowConflict
from app.llm.fake import FakeLlm
from schema.schemas import Agent1Input


class MemoryHost:
    """Example of the required host CAS contract, intentionally test-only."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.receipts, self.updates, self.before_commit = {}, [], None

    def load(self, case_id):
        assert case_id == self.snapshot.a1_input.context.case_id
        return self.snapshot.model_copy(deep=True)

    def read_operation(self, case_id, operation_id):
        value = self.receipts.get(operation_id)
        return value.model_copy(deep=True) if value else None

    def commit(self, case_id, expected_revision, update, operation_id, receipt):
        if self.before_commit:
            callback, self.before_commit = self.before_commit, None
            callback()
        if self.snapshot.state_revision != expected_revision:
            raise WorkflowConflict("host CAS failed")
        if update:
            self.snapshot.a3_ref = update.a3_ref.model_copy(deep=True)
            self.snapshot.rate_card_id = update.rate_card_id
            self.snapshot.state_revision += 1
            self.updates.append(update.model_copy(deep=True))
        self.receipts[operation_id] = receipt.model_copy(deep=True)

    def advance(self):
        self.snapshot.state_revision += 1


@pytest.fixture
def host(a3_input):
    return MemoryHost(Agent3CaseSnapshot(state_revision=1,
        a1_result_id="a1-fixture-result",
        a1_input=Agent1Input(context=a3_input.context, received_at=a3_input.received_at,
            sender_role="PM", message_text="합성 요청 원문"), items=a3_input.items,
        a2_result=a3_input.a2_result, rate_card_id=a3_input.rate_card_id,
        constraints=a3_input.constraints, answers=a3_input.answers))


@pytest.fixture
def adapter(host, a3_payload):
    model = FakeLlm([copy.deepcopy(a3_payload)] * 20)
    prose = FakeLlm([copy.deepcopy(FAKE_EMAIL)] * 20)
    return Agent3SupervisorAdapter(host, lambda snapshot: Agent3Orchestration(LlmAgent3(model), draft_llm=prose))


def estimate(adapter, host, op="estimate", ids=None, revision=None):
    return adapter.execute(host.snapshot.a1_input.context.case_id,
        host.snapshot.state_revision if revision is None else revision, op,
        {"action": "estimate_impact", "args": {"item_ids": ids or host.snapshot.input().target_item_ids}, "reason": "잠정 산정"})


def document(adapter, host, kind="negotiation_email", op="documents", ids=None):
    return adapter.execute(host.snapshot.a1_input.context.case_id, host.snapshot.state_revision, op,
        {"action": "draft_documents", "args": {"kind": kind, "item_ids": ids or [i.item_id for i in host.snapshot.items]}, "reason": "고객 초안 작성"})


def pm(adapter, host, command, op="pm", actor="authenticated-pm"):
    return adapter.execute_pm(host.snapshot.a1_input.context.case_id, host.snapshot.state_revision,
        op, command, actor_ref=actor)


def approve(adapter, host, op="approve"):
    return pm(adapter, host, {"action": "review", "expected_snapshot_hash": host.snapshot.a3_ref.snapshot_hash,
        "decision": "approve", "acknowledge_unknown_schedule": True}, op=op)


def test_id_tools_build_input_from_host_and_commit_reference(adapter, host):
    result = estimate(adapter, host)
    assert result.status == "ready" and result.error is None
    assert result.processed_item_ids == host.snapshot.input().target_item_ids
    assert host.snapshot.state_revision == 2
    update = host.updates[-1]
    assert update.view["impact"]["estimates"][0]["totals"]["price"] == 5150000
    assert update.view["approval"] is None
    assert update.a3_ref.snapshot_hash == update.view["snapshot_hash"]
    assert "session_id" not in result.model_dump()
    assert [s["name"] for s in tool_schemas()] == ["estimate_impact", "draft_documents"]
    assert "context" not in tool_schemas()[0]["parameters"]["properties"]


def test_supervisor_cannot_set_runtime_or_approve(adapter, host):
    raw = {"action": "estimate_impact", "args": {"item_ids": host.snapshot.input().target_item_ids,
        "rate_card_id": "forged"}, "reason": "test"}
    result = adapter.execute(host.snapshot.a1_input.context.case_id, 1, "bad", raw)
    assert result.error.code == "INVALID_ARGUMENT" and not host.updates
    raw = {"action": "review", "decision": "approve"}
    assert adapter.execute(host.snapshot.a1_input.context.case_id, 1, "bad-review", raw).error.code == "INVALID_ARGUMENT"


def test_operation_replay_is_idempotent_and_reuse_rejected(adapter, host):
    first = estimate(adapter, host)
    assert estimate(adapter, host, revision=1) == first
    assert len(host.updates) == 1
    reused = estimate(adapter, host)
    assert reused.error.code == "OPERATION_CONFLICT"


def test_unchanged_estimate_uses_saved_result_without_new_model_call(adapter, host):
    estimate(adapter, host)
    first_view = host.updates[-1].view
    second = estimate(adapter, host, op="cached")
    assert second.status == "ready" and second.processed_item_ids == []
    assert second.result_ids == [first_view["impact"]["meta"]["result_id"]]
    assert len(host.updates) == 1


def test_document_kind_mapping_preserves_complete_bundle(adapter, host):
    estimate(adapter, host)
    email = document(adapter, host)
    view = host.updates[-1].view
    assert set(view["documents"]) == {"email", "agreement"}
    assert email.result_ids == [view["documents"]["email"]["result_id"]]
    revision = host.snapshot.state_revision
    agreement = document(adapter, host, "change_agreement", op="agreement")
    assert agreement.result_ids == [view["documents"]["agreement"]["result_id"]]
    assert host.snapshot.state_revision == revision
    assert approve(adapter, host).status == "ready"
    assert host.updates[-1].view["phase"] == "FINAL_APPROVED"
    assert host.updates[-1].view["approval"]["reviewer_ref"] == "authenticated-pm"


def test_unknown_ids_and_partial_document_scope_rejected(adapter, host):
    assert estimate(adapter, host, ids=["other-case"]).error.code == "INVALID_ARGUMENT"
    estimate(adapter, host)
    assert document(adapter, host, ids=["other-case"]).error.code == "UNSUPPORTED_DOCUMENT_SCOPE"
    assert len(host.updates) == 1


def test_first_execution_requires_entire_target_set(adapter, host):
    other = host.snapshot.items[0].model_copy(deep=True)
    other.item_id += "-extra"
    host.snapshot.items.append(other)
    result = estimate(adapter, host, ids=[host.snapshot.items[0].item_id])
    assert result.error.code == "REQUIRES_FULL_REESTIMATION"
    assert not host.updates


def test_stale_host_revision_rejected_before_model_call(adapter, host):
    assert estimate(adapter, host, revision=0).error.code == "STATE_CONFLICT"
    assert not host.updates


def test_cas_failure_does_not_publish_tool_mutation(adapter, host):
    estimate(adapter, host)
    old_ref = host.snapshot.a3_ref.model_copy(deep=True)
    host.before_commit = host.advance
    result = document(adapter, host)
    assert result.error.code == "STATE_CONFLICT"
    key = (host.snapshot.a1_input.context.project_id, host.snapshot.a1_input.context.case_id)
    actual = adapter._services[key].get(old_ref.session_id)
    assert actual["state_revision"] == old_ref.state_revision and not actual["documents"]
    assert "documents" not in host.receipts


def test_model_error_returns_retryable_failure_without_state_change(host):
    from app.agents.base import AgentFailure
    from schema.schemas import AgentError
    class BadAgent:
        llm = None
        def run(self, inp):
            raise AgentFailure(AgentError(code="OUTPUT_INVALID", message="invalid model coverage", stage="agent3"))
    adapter = Agent3SupervisorAdapter(host, lambda snapshot: Agent3Orchestration(BadAgent()))
    result = estimate(adapter, host)
    assert result.error.code == "OUTPUT_INVALID" and result.error.retryable
    assert not host.updates


def test_needs_envelope_routes_document_research_and_has_stable_id(adapter, host):
    host.snapshot.a2_result.assessments[0].evidence = []
    host.snapshot.a2_result.assessments[0].label = "unclear"
    host.snapshot.a2_result.assessments[0].evidence_level = "none"
    host.snapshot.a2_result.assessments[0].missing_info = ["근거 필요"]
    result = estimate(adapter, host)
    assert result.status == "needs_input"
    assert result.needs[0]["target"] == "agent2" and result.needs[0]["need_id"]
    assert estimate(adapter, host, op="repeat").needs[0]["need_id"] == result.needs[0]["need_id"]
    assert host.updates[-1].view["approval"] is None


def test_pm_prose_edit_invalidates_approval_preserving_numbers_and_other_document(adapter, host):
    estimate(adapter, host)
    document(adapter, host)
    approve(adapter, host)
    original = host.updates[-1].view
    result = pm(adapter, host, {"action": "edit_document", "kind": "negotiation_email",
        "narrative": "안녕하세요. 검토 내용을 안내드립니다.\n\n[[APPROVED_FACTS]]\n\n검토 부탁드립니다.", "reason": "PM 문안 검토"}, op="edit")
    view = host.updates[-1].view
    assert result.status == "ready" and view["approval"] is None
    assert "5,150,000원" in view["documents"]["email"]["body"]
    assert view["documents"]["agreement"] == original["documents"]["agreement"]
    assert view["documents"]["email"]["model"] == "pm-edited"
    assert approve(adapter, host, op="reapprove").status == "ready"


def test_pm_cannot_edit_numeric_facts_or_use_stale_approval(adapter, host):
    estimate(adapter, host)
    document(adapter, host)
    old_hash = host.snapshot.a3_ref.snapshot_hash
    result = pm(adapter, host, {"action": "edit_document", "kind": "negotiation_email",
        "narrative": "100원으로 진행합니다. [[APPROVED_FACTS]]", "reason": "수정"})
    assert result.error.code == "INVALID_ARGUMENT"
    result = pm(adapter, host, {"action": "review", "decision": "approve", "expected_snapshot_hash": old_hash + "stale"}, op="stale")
    assert result.error.code == "STATE_CONFLICT"
    assert pm(adapter, host, {"action": "review", "decision": "approve", "expected_snapshot_hash": old_hash}, actor="").error.code == "INVALID_ARGUMENT"


def test_pm_calculation_uses_no_model_and_updates_host_rate_reference(adapter, host):
    estimate(adapter, host)
    document(adapter, host)
    result = pm(adapter, host, {"action": "recalculate", "args": {
        "edits": [{"work_id": "0:0", "md": 4.5}], "rate_card_id": "RC-SYNTH-2026H2-v2", "reason": "공수·단가 검토"}})
    assert result.status == "ready"
    assert host.snapshot.rate_card_id == "RC-SYNTH-2026H2-v2"
    assert not host.updates[-1].view["documents"]
    assert document(adapter, host, op="new-documents").status == "ready"


def test_adapter_restart_requires_explicit_full_estimation(adapter, host, a3_payload):
    estimate(adapter, host)
    restarted = Agent3SupervisorAdapter(host, lambda snapshot: Agent3Orchestration(
        LlmAgent3(FakeLlm([a3_payload])), draft_llm=FakeLlm([FAKE_EMAIL])))
    assert document(restarted, host, op="restart-doc").error.code == "UPSTREAM_NOT_READY"
    assert estimate(restarted, host, op="restart-estimate").status == "ready"


def test_scope_reply_without_impact_model_call(adapter, host):
    host.snapshot.items[0].intent = "confirmation"
    host.snapshot.a2_result.assessments[0].label = "in_scope"
    result = document(adapter, host, kind="scope_response")
    assert result.status == "ready"
    assert not host.updates[-1].view["impact"]["estimates"]
    assert set(host.updates[-1].view["documents"]) == {"email"}


def test_partial_estimate_adapter_keeps_other_item_and_reports_processed_scope(host):
    from app.agents.agent3.effort import load_effort_rules
    first_id = host.snapshot.items[0].item_id
    second = host.snapshot.items[0].model_copy(deep=True)
    second.item_id += "-second"
    host.snapshot.items.append(second)
    assessment = host.snapshot.a2_result.assessments[0].model_copy(deep=True)
    assessment.item_id = second.item_id
    host.snapshot.a2_result.assessments.append(assessment)
    rule = next(k for k, v in load_effort_rules()["rules"].items() if v.get("kind") == "general_test" and v["role"] == "BE")
    def row(n):
        return {"item_numbers": [n], "status": "draft", "lines": [{"task": f"작업 {n}", "item_numbers": [n], "role": "BE", "rule_id": rule}]}
    payloads = [{"estimates": [row(1), row(2)], "questions": []}, {"estimates": [row(1)], "questions": []}]
    model = FakeLlm(payloads)
    adapter = Agent3SupervisorAdapter(host, lambda snapshot: Agent3Orchestration(LlmAgent3(model)))
    assert estimate(adapter, host).status == "ready"
    untouched = copy.deepcopy(host.updates[-1].view["impact"]["estimates"][1])
    host.advance()
    host.snapshot.items[0].text += " 변경된 조건"
    host.snapshot.a1_result_id += "-new"
    host.snapshot.a2_result.meta.result_id += "-new"
    result = estimate(adapter, host, op="partial", ids=[first_id])
    assert result.status == "ready", result
    assert result.processed_item_ids == [first_id]
    assert untouched in host.updates[-1].view["impact"]["estimates"]
    assert host.updates[-1].source_result_ids["agent1"] == host.snapshot.a1_result_id


def test_rechecked_question_resolution_is_reported_to_host(host, a3_payload):
    first = copy.deepcopy(a3_payload)
    first["questions"] = [{"item_numbers": [1], "field": "storage", "text": "저장 조건을 확인해 주세요.",
        "reason": "필수 확인", "target": "client", "blocking": True}]
    model = FakeLlm([first, a3_payload])
    adapter = Agent3SupervisorAdapter(host, lambda snapshot: Agent3Orchestration(LlmAgent3(model)))
    assert estimate(adapter, host).status == "needs_input"
    question_id = host.updates[-1].active_questions[0].question_id
    host.advance()
    host.snapshot.a2_result.meta.result_id += "-investigated"
    assert estimate(adapter, host, op="rechecked").status == "ready"
    assert host.updates[-1].resolved_question_ids == [question_id]


def test_changed_model_configuration_requires_a_new_full_estimate(adapter, host):
    estimate(adapter, host)
    old_sid = host.snapshot.a3_ref.session_id
    host.advance()
    host.snapshot.execution_config_revision = "ollama-test-config"
    assert document(adapter, host, op="config-doc").error.code == "UPSTREAM_NOT_READY"
    assert estimate(adapter, host, op="new-config").status == "ready"
    assert host.snapshot.a3_ref.session_id != old_sid
    assert host.snapshot.a3_ref.execution_config_revision == "ollama-test-config"


def test_document_edit_api_rechecks_numbers_and_preserves_bundle(host, a3_payload, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import agent3_routes
    from app.main import app
    tools = Agent3Orchestration(LlmAgent3(FakeLlm([a3_payload])), draft_llm=FakeLlm([FAKE_EMAIL]))
    view = tools.estimate_impact(host.snapshot.input())
    from app.agents.agent3.orchestration import DraftDocuments
    view = tools.draft_documents(view["session_id"], DraftDocuments(state_revision=1))
    monkeypatch.setattr(agent3_routes, "get_orchestration", lambda: tools)
    body = {"state_revision": view["state_revision"], "kind": "email", "narrative": "안녕하세요.\n[[APPROVED_FACTS]]\n검토 부탁드립니다.", "editor_ref": "trusted-pm", "reason": "문안 검수"}
    with TestClient(app) as http:
        response = http.post(f"/agent3/orchestration/{view['session_id']}/documents/edit", json=body)
        assert response.status_code == 200
        assert set(response.json()["documents"]) == {"email", "agreement"}
        assert "5,150,000원" in response.json()["documents"]["email"]["body"]
        assert http.post(f"/agent3/orchestration/{view['session_id']}/documents/edit", json=body).status_code == 409
