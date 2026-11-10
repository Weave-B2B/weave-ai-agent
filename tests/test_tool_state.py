import pytest
from test_agent3_agent import a3_input

from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot, Agent2Update, OperationReceipt, ToolResult
from app.agents.agent3.workflow import WorkflowConflict
from app.orchestration.tool_state import SharedToolState
from schema.schemas import Agent1Input


def test_agent2_commit_is_visible_to_agent3_and_stale_write_rejected(a3_input):
    context = a3_input.context
    a1 = Agent1Input(context=context, received_at=a3_input.received_at, sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=context, message_text=a1.message_text,
                              a1_result_id="a1-result", items=a3_input.items)
    host = SharedToolState(snap, a1, a3_input.rate_card_id)
    a2, a3 = host.port("agent2"), host.port("agent3")
    with pytest.raises(WorkflowConflict):
        a3.load(context.case_id)
    update = Agent2Update(a2_result=a3_input.a2_result, a2_item_hashes={}, source_result_ids={}, active_questions=[])
    receipt = OperationReceipt(request_hash="hash", result=ToolResult(operation_id="op", status="ready", summary="완료"))
    a2.commit(context.case_id, 0, update, "op", receipt)
    view = a3.load(context.case_id)
    assert view.state_revision == 1 and view.a2_result == a3_input.a2_result
    view.items.clear()
    assert host.a2.items
    with pytest.raises(WorkflowConflict):
        a2.commit(context.case_id, 0, update, "old", receipt)


def test_unchanged_reassessment_preserves_blocking_agent3_needs_and_impact(a3_input):
    context = a3_input.context
    a1 = Agent1Input(context=context, received_at=a3_input.received_at, sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=context, message_text=a1.message_text,
                              a1_result_id="a1-result", items=a3_input.items, a2_result=a3_input.a2_result)
    host = SharedToolState(snap, a1, a3_input.rate_card_id)
    host.a3_view = {"impact": {"meta": {"result_id": "a3-est-1"}}}
    item_id = a3_input.items[0].item_id
    host.replace_needs("agent3", [{
        "need_id": "a3-need-1",
        "kind": "more_evidence",
        "item_ids": [item_id],
        "topic": "기존 업로드 모듈 재사용 여부 확인",
        "target": "agent2",
        "blocking": True,
    }])

    same_substantive = a3_input.a2_result.model_copy(deep=True)
    same_substantive.meta.result_id = "a2-reassessed-same"
    update = Agent2Update(a2_result=same_substantive, a2_item_hashes={}, source_result_ids={}, active_questions=[])
    receipt = OperationReceipt(
        request_hash="hash-reassess",
        result=ToolResult(operation_id="op-reassess", status="ready", processed_item_ids=[item_id], summary="완료"),
    )
    host.port("agent2").commit(context.case_id, 0, update, "op-reassess", receipt)

    assert host.reassess_counts[item_id] == 1
    assert host.a2_no_progress is True
    assert host.a3_stale is False
    assert any(n.get("source_tool") == "agent3" and n.get("blocking") for n in host.last_needs)
