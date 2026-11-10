"""Draft handoff: translate an Agent 3 research need into an Agent 2 tool action.

The orchestrator chooses when to use it. This helper never calls another agent,
changes host state, or grants access to a baseline.
"""
from app.agents.agent2.orchestrator_adapter import AssessAction
from app.agents.agent2.orchestrator_adapter import Agent2OrchestratorAdapter, ToolResult
from app.agents.agent3.contracts import Agent3Need


def propose_scope_research(need: Agent3Need, allowed_item_ids: set[str]) -> AssessAction:
    """Build an ID-only proposal from a trusted snapshot and a more_evidence signal."""
    if need.kind != "more_evidence" or need.target != "agent2":
        raise ValueError("Agent 2 research requires a more_evidence need")
    ids = need.item_ids
    if len(ids) != len(set(ids)) or not set(ids) <= allowed_item_ids:
        raise ValueError("research items must be distinct and belong to the current case")
    topic = need.topic.strip()
    if not topic or len(topic) > 200:
        raise ValueError("research focus must contain 1 to 200 characters")
    return AssessAction(action="assess_scope", args={"item_ids": ids, "focus": topic},
                        reason="산정에 필요한 계약 근거를 추가 확인합니다.")


def execute_scope_research(*, adapter: Agent2OrchestratorAdapter, case_id: str,
                           state_revision: int, operation_id: str, need: Agent3Need,
                           allowed_item_ids: set[str]) -> ToolResult:
    """Execute a chosen investigation via the trusted host's Agent 2 adapter.

    The caller owns selection and the shared state. A successful search does not
    by itself prove that the need is resolved or authorize a new estimate.
    """
    action = propose_scope_research(need, allowed_item_ids)
    return adapter.execute(case_id, state_revision, operation_id, action.model_dump())
