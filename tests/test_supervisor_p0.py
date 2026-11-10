"""Regression checks for the local Supervisor's P0 integration contract.

No real provider requests; FakeLlm and synthetic fixtures only.
"""
import copy

import pytest

from test_agent2_orchestrator_adapter import CountingAgent
from test_agent3_agent import a3_input, a3_payload
from app.agents.agent1.agent import LlmAgent1
from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot, Agent2OrchestratorAdapter, Agent2Update, OperationReceipt, ToolResult
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.orchestration import Agent3Orchestration
from app.agents.agent3.orchestrator_adapter import Agent3OrchestratorAdapter
from app.agents.agent3.workflow import FAKE_EMAIL, WorkflowConflict
from app.agents.fixture import FixtureAgent1, load_chain
from app.llm.fake import FakeLlm
from app.orchestration.supervisor_runtime import SupervisorRuntime
from app.orchestration.supervisor_start import start_supervisor
from app.orchestration.tool_state import SharedToolState
from schema.schemas import Agent1Input, Question


def test_agent1_blocking_question_is_answerable_and_reanalyzed():
    chain = load_chain()
    inp = Agent1Input.model_validate(chain["CR-A-001"]["agent1_input"])
    fixture = FixtureAgent1(chain)

    class PendingAgent1:
        def run(self, request):
            output = fixture.run(request)
            if request.answers:
                return output
            q = Question(question_id=inp.context.case_id + "-A1Q", case_id=inp.context.case_id,
                stage="agent1", item_ids=[output.items[0].item_id], field="requested_feature",
                text="추가할 기능을 설명해 주세요.", reason="기능 세부 조건 미확인",
                target="pm", blocking=True)
            return output.model_copy(update={"status": "NEEDS_INPUT", "questions": [q]})

        def analyze_answer(self, request):
            return LlmAgent1(FakeLlm([])).analyze_answer(request)

    runtime = start_supervisor(inp, "RC-SYNTH-2026H2-v1", agent1=PendingAgent1(),
        agent2=CountingAgent("CR-A-001"), agent3=None,
        llm=FakeLlm([{"action": "analyze_request",
                      "args": {"mode": "answer", "question_id": inp.context.case_id + "-A1Q"},
                      "reason": "PM 답변 재분석"}]), draft_llm=FakeLlm([]))
    assert runtime.step()["status"] == "waiting_input"
    question = runtime.state.pending_question
    revision = runtime.answer(question["question_id"], "리뷰 기능을 추가해주세요.", 0)
    assert revision == 1
    event = runtime.step()
    assert event["action"] == "analyze_request"
    assert event["status"] == "ready"
    assert runtime.state.pending_question is None
    assert runtime.state.a2.a2_result is None
    assert runtime.state.a2.a1_result_id == runtime.state.a1_result.meta.result_id


def test_needs_are_merged_by_source_not_replaced(a3_input):
    inp = Agent1Input(context=a3_input.context, received_at=a3_input.received_at,
                      sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=a3_input.context,
        message_text=inp.message_text, a1_result_id="a1", items=a3_input.items)
    state = SharedToolState(snap, inp, a3_input.rate_card_id)
    state.replace_needs("agent1", [{"question_id": "q1", "blocking": True, "target": "pm"}])
    state.replace_needs("agent2", [{"question_id": "q2", "blocking": True, "target": "pm"}])
    state.replace_needs("agent3", [{"question_id": "q3", "blocking": True, "target": "pm"}])
    assert {x["question_id"] for x in state.last_needs} == {"q1", "q2", "q3"}
    state.replace_needs("agent3", [])
    assert {x["question_id"] for x in state.last_needs} == {"q1", "q2"}


def test_agent2_changed_result_marks_previous_impact_stale(a3_input):
    inp = Agent1Input(context=a3_input.context, received_at=a3_input.received_at,
                      sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=a3_input.context,
        message_text=inp.message_text, a1_result_id="a1", items=a3_input.items,
        a2_result=a3_input.a2_result)
    state = SharedToolState(snap, inp, a3_input.rate_card_id)
    state.a3_view = {"impact": {"meta": {"result_id": "stale-estimate"}}}
    changed = a3_input.a2_result.model_copy(deep=True)
    changed.meta.result_id = "new-scope-result"
    update = Agent2Update(a2_result=changed, a2_item_hashes={},
        source_result_ids={}, active_questions=[])
    receipt = OperationReceipt(request_hash="test",
        result=ToolResult(operation_id="op", status="ready", summary="ready"))
    state.port("agent2").commit(a3_input.context.case_id, 0, update, "op", receipt)
    assert state.a3_stale
    assert state.a3_view["impact"]["meta"]["result_id"] == "stale-estimate"
    assert state.final_review is None


def test_final_review_is_server_validated_and_version_locked(a3_input, a3_payload):
    inp = Agent1Input(context=a3_input.context, received_at=a3_input.received_at,
                      sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=a3_input.context,
        message_text=inp.message_text, a1_result_id="a1", items=a3_input.items,
        a2_result=a3_input.a2_result)
    state = SharedToolState(snap, inp, a3_input.rate_card_id)
    a2 = Agent2OrchestratorAdapter(state.port("agent2"), lambda _: CountingAgent("CR-A-001"))
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)] * 4))
    a3 = Agent3OrchestratorAdapter(state.port("agent3"),
        lambda _: Agent3Orchestration(worker, draft_llm=FakeLlm([copy.deepcopy(FAKE_EMAIL)] * 4)))
    ids = [item.item_id for item in a3_input.items]
    decisions = [
        {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "잠정 산정"},
        {"action": "draft_documents", "args": {"kind": "negotiation_email", "item_ids": ids}, "reason": "고객 문서"},
    ]
    runtime = SupervisorRuntime(state, a2, a3, FakeLlm(decisions))
    assert runtime.step()["status"] == "ready"
    assert runtime.step()["status"] == "ready"
    assert runtime.step()["status"] == "waiting_final_review"
    snapshot = state.a3_view["snapshot_hash"]
    with pytest.raises(WorkflowConflict):
        runtime.review(decision="approve", state_revision=0, expected_snapshot_hash=snapshot)
    with pytest.raises(WorkflowConflict):
        runtime.review(decision="approve", state_revision=state.a2.state_revision,
                       expected_snapshot_hash="stale")
    approved = runtime.review(decision="approve", state_revision=state.a2.state_revision,
        expected_snapshot_hash=snapshot, acknowledge_unknown_schedule=True)
    assert approved["decision"] == "approve"
    assert state.final_review["snapshot_hash"] == snapshot
    assert runtime.step()["status"] == "completed"
