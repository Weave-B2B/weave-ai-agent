import copy
import pytest

from test_agent3_agent import a3_input, a3_payload
from test_agent2_orchestrator_adapter import CountingAgent
from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot, Agent2OrchestratorAdapter
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.orchestration import Agent3Orchestration
from app.agents.agent3.orchestrator_adapter import Agent3OrchestratorAdapter
from app.llm.fake import FakeLlm
from app.orchestration.supervisor_runtime import SupervisorRuntime
from app.orchestration.tool_state import SharedToolState
from schema.schemas import Agent1Input
from app.agents.agent3.workflow import WorkflowConflict, FAKE_EMAIL
from app.agents.fixture import FixtureAgent1, load_chain
from app.orchestration.supervisor_start import start_supervisor


def test_supervisor_dispatches_research_then_reestimates(a3_input, a3_payload):
    ctx = a3_input.context
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="합成 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=ctx, message_text=a1.message_text,
        a1_result_id="a1-result", items=a3_input.items, a2_result=a3_input.a2_result)
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    agent2 = Agent2OrchestratorAdapter(state.port("agent2"), lambda _: CountingAgent("CR-A-001"))
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)] * 4))
    agent3 = Agent3OrchestratorAdapter(state.port("agent3"), lambda _: Agent3Orchestration(worker, draft_llm=worker.llm))
    ids = [a3_input.items[0].item_id]
    decisions = [
        {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "영향 산정"},
        {"action": "assess_scope", "args": {"item_ids": ids, "focus": "리뷰 범위"}, "reason": "계약 확인"},
        {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "변경 근거 반영"},
    ]
    runtime = SupervisorRuntime(state, agent2, agent3, FakeLlm(decisions))
    first = runtime.step()
    assert first["status"] == "ready", first
    old_id = state.a2.a2_result.meta.result_id
    researched = runtime.step()
    assert researched["status"] == "ready", researched
    assert state.a2.a2_result.meta.result_id != old_id
    estimate = runtime.step()
    assert estimate["status"] == "ready", estimate
    assert state.a3_view["impact"]["meta"]["upstream_result_ids"]


def test_pm_question_waits_for_answer_and_resumes_research(a3_input):
    ctx = a3_input.context
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=ctx, message_text=a1.message_text,
        a1_result_id="a1-result", items=a3_input.items, a2_result=a3_input.a2_result)
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    ids = [a3_input.items[0].item_id]
    state.last_needs = [{"question_id": "Q1", "kind": "client_input", "target": "pm", "blocking": True, "topic": "사진 범위는?", "item_ids": ids}]
    agent2 = Agent2OrchestratorAdapter(state.port("agent2"), lambda _: CountingAgent("CR-A-001"))
    llm = FakeLlm([{"action": "ask_pm", "args": {"question_id": "Q1"}, "reason": "범위 확인"},
                   {"action": "assess_scope", "args": {"item_ids": ids, "focus": "사진 조건"}, "reason": "답변 반영"}])
    runtime = SupervisorRuntime(state, agent2, None, llm)
    waiting = runtime.step()
    assert waiting["status"] == "waiting_input"
    assert runtime.step()["status"] == "waiting_input" and len(llm.calls) == 1
    with pytest.raises(WorkflowConflict):
        runtime.answer("Q1", "사진 3장", 0)
    with pytest.raises(ValueError):
        runtime.answer("Q1", " ", waiting["state_revision"])
    revision = runtime.answer("Q1", "사진 3장", waiting["state_revision"])
    assert state.answers_pending_review and state.a2.answers[0].question_id == "Q1"
    assert revision == 2
    assert runtime.step()["status"] == "ready"
    assert not state.answers_pending_review


def test_supervisor_can_hold_without_executing_tools(a3_input):
    ctx = a3_input.context
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(state_revision=0, context=ctx, message_text=a1.message_text,
        a1_result_id="a1-result", items=a3_input.items)
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    llm = FakeLlm([{"action": "hold", "args": {}, "reason": "계약 문서 확인 필요"}])
    runtime = SupervisorRuntime(state, None, None, llm)
    assert runtime.step()["action"] == "hold"
    assert runtime.step()["status"] == "waiting" and len(llm.calls) == 1


def test_start_links_quote_and_estimates_target(a3_payload):
    chain = load_chain()
    row = chain["CR-A-001"]
    inp = Agent1Input.model_validate(row["agent1_input"])
    ids = [i["item_id"] for i in row["agent1_gold"]["items"] if i["item_type"] == "function"]
    llm = FakeLlm([{"action": "assess_scope", "args": {"item_ids": ids}, "reason": "범위 검토"},
                   {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ids}, "reason": "해당 기능 견적"},
                   {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "영향 산정"}])
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)] * 3))
    runtime = start_supervisor(inp, "RC-SYNTH-2026H2-v1", agent1=FixtureAgent1(chain),
        agent2=CountingAgent("CR-A-001"), agent3=worker, llm=llm, draft_llm=llm)
    assert runtime.state.a1_result.meta.result_id
    assert runtime.step()["status"] == "ready"
    assert runtime.state.a2.a2_result is not None
    assert runtime.step()["action"] == "link_quote"
    result = runtime.step()
    assert result["status"] == "ready", result
    assert runtime.state.a3_view["impact"]["estimates"]


def test_ambiguous_quote_waits_for_pm_then_rechecks(a3_payload):
    chain = load_chain()
    row = chain["CR-A-001"]
    inp = Agent1Input.model_validate(row["agent1_input"])
    ids = [i["item_id"] for i in row["agent1_gold"]["items"] if i["item_type"] == "function"]
    llm = FakeLlm([{"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": []}, "reason": "대상 모호"},
                   {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ids}, "reason": "PM 답변 반영"}])
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)]))
    runtime = start_supervisor(inp, "RC-SYNTH-2026H2-v1", agent1=FixtureAgent1(chain),
        agent2=CountingAgent("CR-A-001"), agent3=worker, llm=llm, draft_llm=llm)
    result = runtime.step()
    assert result["status"] == "waiting_input" and not runtime.state.quote_links
    runtime.answer(result["question"]["question_id"], "리뷰 기능입니다", result["state_revision"])
    assert runtime.step()["status"] == "ready"
    assert runtime.state.quote_links == {"CR-A-001-3": ids}


def test_quote_can_link_multiple_items_and_rejects_quote_as_target(a3_input):
    ctx = a3_input.context
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="리뷰와 결제 기능 견적")
    first = a3_input.items[0].model_copy(deep=True)
    second = first.model_copy(update={"item_id": ctx.case_id + "-payment", "text": "결제 기능"})
    quote = first.model_copy(update={"item_id": ctx.case_id + "-quote", "intent": "estimate_request", "item_type": "question", "text": "견적"})
    snap = Agent2CaseSnapshot(state_revision=0, context=ctx, message_text=a1.message_text,
                              a1_result_id="a1-result", items=[first, second, quote])
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    llm = FakeLlm([{"action": "link_quote", "args": {"quote_item_id": quote.item_id, "item_ids": [quote.item_id]}, "reason": "검증"},
                   {"action": "link_quote", "args": {"quote_item_id": quote.item_id, "item_ids": [first.item_id, second.item_id]}, "reason": "두 기능"}])
    runtime = SupervisorRuntime(state, None, None, llm)
    assert runtime.step()["status"] == "error" and not state.quote_links
    assert runtime.step()["status"] == "ready"
    assert state.quote_links[quote.item_id] == [first.item_id, second.item_id]


def test_optional_agent2_question_is_promoted_to_pm_when_agent3_is_blocked(a3_input):
    """When Agent 2 leaves an optional question and Agent 3 blocks on the same item, promote to ask_pm instead of re-searching."""
    ctx = a3_input.context
    item_id = a3_input.items[0].item_id
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="사진 업로드 추가")
    snap = Agent2CaseSnapshot(
        state_revision=0,
        context=ctx,
        message_text=a1.message_text,
        a1_result_id="a1-result",
        items=a3_input.items,
        a2_result=a3_input.a2_result,
    )
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    state.replace_needs("agent2", [{
        "need_id": "a2-opt-q",
        "question_id": "Q-REUSE",
        "kind": "client_input",
        "target": "client",
        "blocking": False,
        "topic": "기존 업로드 기능을 재사용할 수 있나요?",
        "item_ids": [item_id],
    }])
    state.replace_needs("agent3", [{
        "need_id": "a3-block-ev",
        "kind": "more_evidence",
        "target": "agent2",
        "blocking": True,
        "topic": "계약 조건·제외 범위 확인",
        "item_ids": [item_id],
    }])
    llm = FakeLlm([
        # First attempt tries to re-run assess_scope on the blocked item, which must be rejected because it's now PM-blocked
        {"action": "assess_scope", "args": {"item_ids": [item_id], "focus": "재검색"}, "reason": "재검색 시도"},
        {"action": "ask_pm", "args": {"question_id": "Q-REUSE"}, "reason": "PM에게 재사용 가능 여부 확인"},
    ])
    runtime = SupervisorRuntime(state, None, None, llm)
    event = runtime.step()
    assert event["status"] == "waiting_input"
    assert event["action"] == "ask_pm"
    assert event["question"]["question_id"] == "Q-REUSE"
    assert event["question"]["blocking"] is True
    assert event["question"]["target"] == "pm"
    assert not any(n.get("kind") == "more_evidence" for n in state.last_needs)


def test_reassess_cap_prevents_infinite_agent2_agent3_ping_pong(a3_input):
    """After 1 focus reassessment on an unclear item, further assess_scope is blocked and the supervisor holds."""
    ctx = a3_input.context
    item_id = a3_input.items[0].item_id
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="사진 업로드 추가")
    unclear_a2 = a3_input.a2_result.model_copy(deep=True)
    unclear_a2.assessments[0] = unclear_a2.assessments[0].model_copy(
        update={"label": "unclear", "missing_info": ["추가 계약 조항 필요"]}
    )
    snap = Agent2CaseSnapshot(
        state_revision=0,
        context=ctx,
        message_text=a1.message_text,
        a1_result_id="a1-result",
        items=a3_input.items,
        a2_result=unclear_a2,
    )
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    state.reassess_counts[item_id] = 1
    state.replace_needs("agent2", [{
        "need_id": "a2-more-ev",
        "kind": "more_evidence",
        "target": "agent2",
        "blocking": False,
        "topic": "추가 근거 부족",
        "item_ids": [item_id],
    }])
    llm = FakeLlm([
        # Trying assess_scope again after exhausting the reassessment cap is rejected
        {"action": "assess_scope", "args": {"item_ids": [item_id], "focus": "또 검색"}, "reason": "반복 검색"},
        {"action": "hold", "args": {}, "reason": "추가 계약 근거가 없어 검토 보류"},
    ])
    runtime = SupervisorRuntime(state, None, None, llm)
    event = runtime.step()
    assert event["status"] == "waiting"
    assert event["action"] == "hold"
    assert state.hold_reason == "추가 계약 근거가 없어 검토 보류"


def test_run_step_budget_exhaustion_forces_graceful_hold(a3_input):
    ctx = a3_input.context
    a1 = Agent1Input(context=ctx, received_at=a3_input.received_at, sender_role="PM", message_text="합성 요청")
    snap = Agent2CaseSnapshot(
        state_revision=0,
        context=ctx,
        message_text=a1.message_text,
        a1_result_id="a1-result",
        items=a3_input.items,
    )
    state = SharedToolState(snap, a1, a3_input.rate_card_id)
    agent2 = Agent2OrchestratorAdapter(state.port("agent2"), lambda _: CountingAgent("CR-A-001"))
    ids = [a3_input.items[0].item_id]
    llm = FakeLlm([
        {"action": "assess_scope", "args": {"item_ids": ids}, "reason": "최초 범위 검토"},
    ])
    runtime = SupervisorRuntime(state, agent2, None, llm)
    events = runtime.run(max_steps=1)
    assert len(events) == 2
    assert events[-1]["status"] == "waiting"
    assert events[-1]["action"] == "hold"
    assert state.hold_reason is not None


def test_agent1_nonblocking_pm_question_promoted_when_item_unclear(a3_input):
    from schema.schemas import Agent1Output, Question

    ctx = a3_input.context
    a1_in = Agent1Input(
        context=ctx,
        received_at=a3_input.received_at,
        sender_role="PM",
        message_text="사진 첨부도 가능하면 좋겠는데, 기존 사진 업로드 기능을 재사용할 수 있는지는 확인이 필요합니다.",
    )
    item1 = a3_input.items[0]
    item2 = item1.model_copy(
        update={
            "item_id": "CR-A-001-2",
            "display_no": "R2",
            "item_type": "question",
            "intent": "confirmation",
            "text": "기존 사진 업로드 기능을 재사용할 수 있는지 확인",
            "source_quote": "기존 사진 업로드 기능을 재사용할 수 있는지는 확인이 필요합니다.",
        }
    )
    a1_q = Question(
        question_id="CR-A-001-A1Q1",
        case_id=ctx.case_id,
        stage="agent1",
        item_ids=["CR-A-001-2"],
        field="기존 사진 업로드 기능 재사용 가능 여부",
        text="기존 사진 업로드 기능을 재사용할 수 있는지 확인이 필요합니다.",
        reason="원문만으로 확정되지 않았습니다.",
        target="pm",
        blocking=False,
        status="open",
    )
    snap = Agent2CaseSnapshot(
        state_revision=0,
        context=ctx,
        message_text=a1_in.message_text,
        a1_result_id="a1-result",
        items=[item1, item2],
    )
    state = SharedToolState(snap, a1_in, a3_input.rate_card_id)
    base_a1 = FixtureAgent1(load_chain()).run(a1_in)
    state.a1_result = Agent1Output(
        meta=base_a1.meta,
        context=ctx,
        status="READY",
        items=[item1, item2],
        questions=[a1_q],
    )

    class TwoItemAgent:
        def __init__(self, base_result):
            self.base = base_result

        def run(self, inp, focus=None):
            out = self.base.model_copy(deep=True)
            a2_second = out.assessments[0].model_copy(
                update={
                    "item_id": "CR-A-001-2",
                    "label": "unclear",
                    "missing_info": ["기존 사진 업로드 기능 재사용 가능 여부"],
                }
            )
            return out.model_copy(
                update={
                    "assessments": [out.assessments[0], a2_second],
                    "questions": [],
                }
            )

    agent2 = Agent2OrchestratorAdapter(state.port("agent2"), lambda _: TwoItemAgent(a3_input.a2_result))
    llm = FakeLlm([
        # Partial assess_scope omitting CR-A-001-2 is rejected on first attempt:
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "일부 항목만 검토 시도"},
        # Retry includes all unassessed scope items:
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1", "CR-A-001-2"]}, "reason": "전체 범위 검토"},
        # Next step: CR-A-001-A1Q1 was promoted to blocking PM question -> ask_pm is allowed!
        {"action": "ask_pm", "args": {"question_id": "CR-A-001-A1Q1"}, "reason": "기존 기능 재사용 가능 여부 PM 확인"},
    ])
    runtime = SupervisorRuntime(state, agent2, None, llm)
    e1 = runtime.step()
    assert e1["action"] == "assess_scope"
    assert e1["status"] == "ready", e1
    e2 = runtime.step()
    assert e2["status"] == "waiting_input"
    assert e2["action"] == "ask_pm"
    assert e2["question"]["question_id"] == "CR-A-001-A1Q1"
    # Answering the promoted question routes to agent2/agent3 without wiping out Agent 2 results:
    rev = runtime.answer("CR-A-001-A1Q1", "재사용 가능합니다.", e2["state_revision"])
    assert rev == e2["state_revision"] + 1
    assert state.a1_refresh_required is False
    assert state.a2.a2_result is not None


def test_complex_quote_and_deadline_draft_documents_full_flow(a3_payload):
    """Complex case: Function + Deadline + Quote request.
    Verifies that draft_documents retries safely when LLM initially specifies only the estimated item,
    and succeeds once all unconstrained request items are provided.
    """
    chain = load_chain()
    row = chain["CR-A-001"]
    inp = Agent1Input.model_validate(row["agent1_input"])
    decisions = [
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "기능 항목 범위 검토"},
        {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ["CR-A-001-1"]}, "reason": "견적 요청을 기능에 연결"},
        {"action": "estimate_impact", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "영향 산정"},
        # Attempt 1: LLM proposes only the estimated item (rejected by schema/decision validation)
        {"action": "draft_documents", "args": {"kind": "negotiation_email", "item_ids": ["CR-A-001-1"]}, "reason": "산정된 기능 문서 작성"},
        # Attempt 2: After receiving feedback with draft_documents_item_ids, LLM corrects to include all items
        {"action": "draft_documents", "args": {"kind": "negotiation_email", "item_ids": ["CR-A-001-1", "CR-A-001-3"]}, "reason": "전체 요청 항목 대상 문서 작성"},
    ]
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)] * 3))
    llm = FakeLlm(decisions)
    draft_llm = FakeLlm([FAKE_EMAIL])
    runtime = start_supervisor(inp, "RC-SYNTH-2026H2-v1", agent1=FixtureAgent1(chain),
                               agent2=CountingAgent("CR-A-001"), agent3=worker, llm=llm, draft_llm=draft_llm)

    # Step 1: assess_scope
    e1 = runtime.step()
    assert e1["status"] == "ready" and e1["action"] == "assess_scope"
    # Step 2: link_quote
    e2 = runtime.step()
    assert e2["status"] == "ready" and e2["action"] == "link_quote"
    # Step 3: estimate_impact
    e3 = runtime.step()
    assert e3["status"] == "ready" and e3["action"] == "estimate_impact"
    # Step 4: draft_documents (retried with feedback and succeeded)
    e4 = runtime.step()
    assert e4["status"] == "ready" and e4["action"] == "draft_documents"
    assert runtime.state.a3_view.get("can_final_review") is True
    assert "email" in runtime.state.a3_view.get("documents", {})
    assert "agreement" in runtime.state.a3_view.get("documents", {})
    # Verify retry feedback was provided to LLM
    assert len(llm.calls) == 5
    retry_prompt = llm.calls[4]["user"]
    assert "previous_error" in retry_prompt
    assert "draft_documents_item_ids" in retry_prompt


def test_complex_quote_draft_documents_retry_exhaustion_stops_safely(a3_payload):
    """When LLM repeatedly fails to specify all items for draft_documents,
    the supervisor returns an error status instead of crashing.
    """
    chain = load_chain()
    row = chain["CR-A-001"]
    inp = Agent1Input.model_validate(row["agent1_input"])
    decisions = [
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "기능 항목 범위 검토"},
        {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ["CR-A-001-1"]}, "reason": "견적 요청 연결"},
        {"action": "estimate_impact", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "영향 산정"},
        # Attempt 1: only estimated item
        {"action": "draft_documents", "args": {"kind": "negotiation_email", "item_ids": ["CR-A-001-1"]}, "reason": "산정 항목만 작성"},
        # Attempt 2: still only estimated item (retry exhausted)
        {"action": "draft_documents", "args": {"kind": "negotiation_email", "item_ids": ["CR-A-001-1"]}, "reason": "재시도 실패"},
    ]
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)] * 3))
    llm = FakeLlm(decisions)
    draft_llm = FakeLlm([FAKE_EMAIL])
    runtime = start_supervisor(inp, "RC-SYNTH-2026H2-v1", agent1=FixtureAgent1(chain),
                               agent2=CountingAgent("CR-A-001"), agent3=worker, llm=llm, draft_llm=draft_llm)

    runtime.step()  # assess_scope
    runtime.step()  # link_quote
    runtime.step()  # estimate_impact
    e4 = runtime.step()  # draft_documents -> failure
    assert e4["status"] == "error"
    assert "총괄의 선택 검증 실패: 재시도 후 검토 대기" in e4["reason"]


def test_pm_answer_preserves_pending_review_across_link_quote_and_blocks_premature_estimate(a3_payload):
    chain = load_chain()
    row = chain["CR-A-001"]
    inp = Agent1Input.model_validate(row["agent1_input"])
    ids = ["CR-A-001-1"]
    worker = LlmAgent3(FakeLlm([copy.deepcopy(a3_payload)] * 4))
    decisions = [
        {"action": "assess_scope", "args": {"item_ids": ids}, "reason": "범위 검토"},
        {"action": "ask_pm", "args": {"question_id": "CR-A-001-Q-fake"}, "reason": "PM 질문"},
        {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ids}, "reason": "견적 연결"},
        {"action": "assess_scope", "args": {"item_ids": ids}, "reason": "PM 답변 반영 범위 재검토"},
        {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "영향 산정"},
    ]
    llm = FakeLlm(decisions)
    runtime = start_supervisor(inp, "RC-SYNTH-2026H2-v1", agent1=FixtureAgent1(chain),
                               agent2=CountingAgent("CR-A-001"), agent3=worker, llm=llm, draft_llm=llm)

    # 1. Initial assess_scope
    s1 = runtime.step()
    assert s1["action"] == "assess_scope"

    # Inject a blocking question
    runtime.state.last_needs.append({
        "need_id": "need-q1", "kind": "client_input", "target": "pm",
        "question_id": "CR-A-001-Q-fake", "blocking": True, "topic": "추가 확인", "item_ids": ids
    })

    # 2. ask_pm
    s2 = runtime.step()
    assert s2["status"] == "waiting_input"
    assert s2["action"] == "ask_pm"

    # PM answers
    rev = runtime.answer("CR-A-001-Q-fake", "신규 구현 필요", s2["state_revision"])
    assert runtime.state.answers_pending_review is True

    # 3. link_quote executes next (because quote is not yet linked)
    s3 = runtime.step()
    assert s3["action"] == "link_quote"
    # CRITICAL: link_quote must NOT clear answers_pending_review!
    assert runtime.state.answers_pending_review is True

    # Next step must choose assess_scope (not estimate_impact) because answers_pending_review is True
    s4 = runtime.step()
    assert s4["action"] == "assess_scope"
    assert runtime.state.answers_pending_review is False

    # Now estimate_impact is allowed and runs
    s5 = runtime.step()
    assert s5["action"] == "estimate_impact"
    assert s5["status"] == "ready"


def test_agent3_selection_excludes_answered_questions(a3_input, a3_payload):
    from schema.schemas import Question, Answer
    from app.agents.agent3.contracts import ProvisionalAgent3Input
    from app.orchestration.preflight import provisional_selection

    item_id = a3_input.items[0].item_id
    qid = f"{item_id}-A2Q-test"
    # Add a blocking question to a2_result
    q = Question(question_id=qid, case_id=a3_input.context.case_id, stage="agent2",
                 item_ids=[item_id], text="추가 확인 필요한가요?", reason="테스트 사유", target="pm", blocking=True, status="open")
    a2_res = a3_input.a2_result.model_copy(update={"questions": [q], "status": "NEEDS_INPUT"})

    # Without answer: item is blocked and deferred has client_input
    raw_unanswered = ProvisionalAgent3Input(
        context=a3_input.context, received_at=a3_input.received_at, rate_card_id=a3_input.rate_card_id,
        items=a3_input.items, constraints=[], a2_result=a2_res, answers=[], target_item_ids=[item_id]
    )
    allowed, deferred = provisional_selection(raw_unanswered)
    assert not allowed
    assert any(d.get("question_id") == qid for d in deferred)

    # With answer: question is excluded from blocking_questions, item can be allowed (if out_of_scope & evidence)
    ans = Answer(answer_id="ans-1", question_id=qid, text="답변 완료", answered_by="pm", answered_at="2026-11-10T00:00:00Z")
    raw_answered = raw_unanswered.model_copy(update={"answers": [ans]})
    allowed_ans, deferred_ans = provisional_selection(raw_answered)
    assert not any(d.get("question_id") == qid for d in deferred_ans)



