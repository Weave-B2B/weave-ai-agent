"""고정 Agent 흐름(A1 → A2 → A3)의 순서·실행 가능 여부 규칙.

Runtime의 실행 로직과 분리했다. 이후 Supervisor가 다음 Agent를 선택하게 되면 next_agent 자리를 대체하고,
blocking_reason(검증)은 그대로 유지한다.
"""
from typing import Optional

from app.orchestration.run_state import AGENT_PREREQUISITES, AGENT_STAGES, RunStateSnapshot
from schema.schemas import Stage


def is_waiting_for_user(state: RunStateSnapshot) -> bool:
    return state.status in ("WAITING_INPUT", "WAITING_PM_REVIEW") or state.pending_review is not None


def blocking_reason(state: RunStateSnapshot, agent: Stage) -> Optional[str]:
    """이 상태에서 agent를 시작할 수 없는 이유. 시작할 수 있으면 None."""
    if is_waiting_for_user(state):
        return "사용자 검토/입력 대기 중"
    if state.agents[agent].status == "RUNNING":
        return f"{agent}가 이미 실행 중"
    for pre in AGENT_PREREQUISITES[agent]:
        dep = state.agents[pre]
        if dep.status != "COMPLETED" or dep.current_result_id is None:
            return f"선행 {pre} 결과가 없음"
        if dep.stale:
            return f"선행 {pre} 결과가 stale"
    return None


def next_agent(state: RunStateSnapshot) -> Optional[Stage]:
    """고정 순서에서 아직 완료되지 않았거나 stale인 첫 Agent. 모두 최신 완료면 None."""
    for agent in AGENT_STAGES:
        a = state.agents[agent]
        if a.status != "COMPLETED" or a.stale:
            return agent
    return None


# 엔진(engine.py)의 범위 필터와 동일한 규칙. Runtime 입력 구성용
SCOPE_INTENTS = {"change_request", "confirmation", "defect", "issue_report", "status_check", "estimate_only"}


def scope_items_of(items):
    return [i for i in items if i.intent in SCOPE_INTENTS]


def constraints_of(items):
    # question(견적 요청) 항목은 A2·A3에 넘기지 않는다
    return [i for i in items if i.item_type in ("constraint", "deadline", "dependency")]
