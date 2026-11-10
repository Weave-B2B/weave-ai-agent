"""Construct an isolated local supervisor run from Agent 1 input."""
from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot, Agent2OrchestratorAdapter
from app.agents.agent3.orchestrator_adapter import Agent3OrchestratorAdapter
from app.agents.agent3.orchestration import Agent3Orchestration
from app.kb.loader import baselines, rate_cards, superseded_by
from app.orchestration.ids import new_result_id
from app.orchestration.supervisor_runtime import SupervisorRuntime
from app.orchestration.tool_state import SharedToolState


def start_supervisor(inp, rate_card_id, *, agent1, agent2, agent3, llm, draft_llm):
    """Use synthetic local baseline/rates; callers own case persistence and API."""
    context = inp.context
    baseline = baselines().get(context.baseline_id or "")
    if not baseline or baseline["project_id"] != context.project_id or baseline["as_of"] != context.baseline_as_of:
        raise ValueError("프로젝트와 기준선이 일치하지 않습니다.")
    if superseded_by(context.baseline_id) or rate_card_id not in rate_cards():
        raise ValueError("기준선 또는 단가표가 유효하지 않습니다.")
    result = agent1.run(inp)
    if result.context != context or result.status == "ERROR":
        raise ValueError("Agent 1 결과를 사용할 수 없습니다.")
    result_id = result.meta.result_id or new_result_id("agent1", context.case_id)
    constraints = [i for i in result.items if i.item_type in ("constraint", "deadline", "dependency")]
    items = [i for i in result.items if i not in constraints]
    snap = Agent2CaseSnapshot(state_revision=0, context=context, message_text=inp.message_text,
                              a1_result_id=result_id, items=items, constraints=constraints, answers=inp.answers)
    state = SharedToolState(snap, inp, rate_card_id)
    state.a1_result = result.model_copy(deep=True)
    state.a1_result.meta.result_id = result_id
    a1_needs = [{**q.model_dump(mode="json"), "question_id": q.question_id,
                 "topic": q.text, "kind": "client_input", "source_tool": "agent1"}
                for q in result.questions if q.status == "open"]
    state.replace_needs("agent1", a1_needs)
    blocking_a1 = [n for n in a1_needs if n.get("blocking")]
    if blocking_a1:
        state.pending_question = blocking_a1[0]
    return wire_supervisor(state, agent1=agent1, agent2=agent2, agent3=agent3, llm=llm, draft_llm=draft_llm)


def wire_supervisor(state, *, agent1, agent2, agent3, llm, draft_llm):
    """Bind worker clients to initial or restored state without running Agent 1 again."""
    a2 = Agent2OrchestratorAdapter(state.port("agent2"), lambda _: agent2)
    a3 = Agent3OrchestratorAdapter(state.port("agent3"), lambda _: Agent3Orchestration(agent3, draft_llm=draft_llm))
    return SupervisorRuntime(state, a2, a3, llm, agent1=agent1)
