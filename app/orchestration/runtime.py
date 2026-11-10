"""규칙 기반 Orchestration Runtime (LLM 없음). 실행 흐름은 AI 서버가, 상태·결과의 영속은 Backend가 소유한다.

한 번의 run() 호출 = get_state → (검사) → start_agent 승인 → Agent 실행 → commit_result → get_state … 를
더 진행할 수 없을 때까지 반복한다. 다음 Agent는 항상 Backend에 커밋된 결과만 입력으로 쓴다.
순서 결정은 workflow_rules, 실행·상태 기록은 이 모듈이 맡는다(이후 Supervisor는 순서 결정만 대체).
"""
import uuid
from typing import Callable, Literal, Optional, Protocol

from pydantic import Field
from schema.schemas import (
    Agent1Input, Agent1Output, Agent2Input, Agent2Output, Agent3Input, Agent3Output, AgentError, Item, Meta,
    PMReview, RunStatus, Stage,
)

from app.agents.agent2.guards import apply_guards
from app.agents.agent3.guards import finalize_estimates
from app.agents.base import AgentFailure, AgentSet
from app.orchestration.ids import new_result_id, now_iso
from app.orchestration.preflight import preflight_a3
from app.orchestration.run_state import (
    AGENT_PREREQUISITES, CommitResultRequest, InvalidStateTransition, OperationConflict, PendingReview,
    RequestReviewRequest, RunStateClient, RunStateSnapshot, StartAgentRequest, StatePersistenceError,
    StateRevisionConflict, _M,
)
from app.orchestration.workflow_rules import blocking_reason, is_waiting_for_user, next_agent, scope_items_of, constraints_of

MAX_ATTEMPTS = 3

Outcome = Literal["COMPLETED", "WAITING_FOR_REVIEW", "FAILED", "STOPPED"]
StopReason = Literal[
    "AGENT_RUNNING", "STATE_CHANGED", "START_REPLAYED", "START_REJECTED", "COMMIT_REJECTED", "COMMIT_FAILED",
    "START_FAILED", "PREFLIGHT_BLOCKED", "NOTHING_TO_ANALYZE",
]


class RunInputs(_M):
    """Run의 최초 입력. 출처(Backend 저장분)는 Backend와 합의 필요."""
    agent1_input: Agent1Input
    rate_card_id: str
    pm_review: Optional[PMReview] = None


class RunInputProvider(Protocol):
    def load(self, run_id: str) -> RunInputs: ...


class ExecutedAgent(_M):
    agent_type: Stage
    result_id: str
    execution_status: Literal["COMPLETED", "FAILED"]
    output_status: Optional[str] = None


class RuntimeResult(_M):
    """결과 payload 전문은 담지 않는다. 필요하면 Backend get_result로 조회한다."""
    run_id: str
    outcome: Outcome
    executed: list[ExecutedAgent] = Field(default_factory=list)
    run_status: RunStatus
    state_revision: int
    pending_review: Optional[PendingReview] = None
    stop_reason: Optional[StopReason] = None
    error: Optional[AgentError] = None
    detail: Optional[str] = None


class _Stop(Exception):
    def __init__(self, reason: StopReason, detail: str = "", error: Optional[AgentError] = None):
        super().__init__(detail)
        self.reason, self.detail, self.error = reason, detail, error


class OrchestrationRuntime:
    def __init__(self, state: RunStateClient, agents: AgentSet, inputs: RunInputProvider,
                 operation_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex, max_attempts: int = MAX_ATTEMPTS):
        self.state, self.agents, self.inputs = state, agents, inputs
        self._new_op, self._max = operation_id_factory, max_attempts

    # ------------------------------------------------------------------ 진입점
    def run(self, run_id: str) -> RuntimeResult:
        executed: list[ExecutedAgent] = []
        conflicts = 0
        try:
            while True:
                snap = self._get_state(run_id)
                if is_waiting_for_user(snap):
                    return self._result(snap, "WAITING_FOR_REVIEW", executed)
                running = [a for a, s in snap.agents.items() if s.status == "RUNNING"]
                if running:
                    raise _Stop("AGENT_RUNNING", f"{running} 실행 중 — RUNNING 복구는 이 단계에서 다루지 않는다")
                agent = next_agent(snap)
                if agent is None:
                    return self._result(snap, "COMPLETED", executed)
                reason = blocking_reason(snap, agent)
                if reason:
                    raise _Stop("START_REJECTED", reason)

                run_inputs = self.inputs.load(run_id)
                review = self._review_needed(agent, snap, run_inputs)
                if review is not None:
                    self._request_review(snap, review[0], review[1], review[2])
                    continue
                agent_in, upstream = self._build_input(agent, run_id, snap, run_inputs)

                try:
                    started = self._start(snap, agent)
                except StateRevisionConflict:
                    conflicts += 1
                    if conflicts >= self._max:
                        raise _Stop("STATE_CHANGED", "start_agent revision 충돌이 반복됨")
                    continue                       # 상태를 다시 읽고 여전히 실행 가능하면 새 operation_id로 재시도
                if started.replayed:
                    raise _Stop("START_REPLAYED", "이미 승인된 start 요청의 재전송 — 중복 실행 방지를 위해 실행하지 않음")

                record, failed = self._execute_and_commit(run_id, agent, agent_in, upstream, started.state_revision,
                                                          run_inputs)
                executed.append(record[0])
                if failed is not None:
                    snap = self._get_state(run_id)
                    return self._result(snap, "FAILED", executed, error=failed)
        except _Stop as stop:
            snap = self._get_state(run_id)
            outcome: Outcome = "FAILED" if stop.error else "STOPPED"
            return self._result(snap, outcome, executed, stop.reason, stop.error, stop.detail)

    # ------------------------------------------------------------------ 입력 구성
    def _review_needed(self, agent: Stage, snap: RunStateSnapshot, ri: RunInputs) -> Optional[tuple[Stage, str, str]]:
        """A3 시작 전 PM 검토가 없으면 (agent_type, target_result_id, reason). 검토 대기는 Agent 상태가 아니다."""
        if agent != "agent3":
            return None
        a2_id = snap.agents["agent2"].current_result_id
        rv = ri.pm_review
        if rv is None or rv.status != "REVIEWED" or rv.target_result_id != a2_id:
            return "agent2", a2_id, "Agent 3 실행 전 PM 검토 필요"
        return None

    def _build_input(self, agent: Stage, run_id: str, snap: RunStateSnapshot, ri: RunInputs):
        upstream = [snap.agents[p].current_result_id for p in AGENT_PREREQUISITES[agent]]
        if agent == "agent1":
            return ri.agent1_input, upstream
        a1 = Agent1Output.model_validate(self.state.get_result(run_id, upstream[0]).result_payload)
        scope = scope_items_of(a1.items)
        if not scope:
            raise _Stop("NOTHING_TO_ANALYZE", "판단·산정할 요청 항목이 없다")
        cons = constraints_of(a1.items)
        ctx = ri.agent1_input.context
        if agent == "agent2":
            return Agent2Input(context=ctx, message_text=ri.agent1_input.message_text, a1_result_id=upstream[0],
                               items=scope, constraints=cons, answers=ri.agent1_input.answers), upstream
        a2 = Agent2Output.model_validate(self.state.get_result(run_id, upstream[1]).result_payload)
        a3_in = Agent3Input(context=ctx, rate_card_id=ri.rate_card_id, received_at=ri.agent1_input.received_at,
                            items=scope, a2_result=a2, pm_review=ri.pm_review, constraints=cons,
                            answers=ri.agent1_input.answers)
        gate = preflight_a3(a3_in)
        if gate is not None:
            if gate.code == "WAITING_REVIEW":
                raise _Stop("PREFLIGHT_BLOCKED", "PM 검토 전")
            raise _Stop("PREFLIGHT_BLOCKED", f"{gate.code}: {gate.reason}")
        return a3_in, upstream

    # ------------------------------------------------------------------ 실행·커밋
    def _execute_and_commit(self, run_id, agent, agent_in, upstream, revision, ri):
        case_id = ri.agent1_input.context.case_id
        result_id = new_result_id(agent, case_id)
        error: Optional[AgentError] = None
        output = None
        try:
            output = self._call_agent(agent, agent_in, ri)
            output = output.model_copy(update={"meta": output.meta.model_copy(update={
                "result_id": result_id, "created_at": now_iso(), "upstream_result_ids": upstream})})
            error = output.error if getattr(output, "error", None) else None
            if error is None and output.status == "ERROR":
                error = AgentError(code="INTERNAL_ERROR", message=f"{agent} 출력 status=ERROR", stage=agent, retryable=False)
        except AgentFailure as e:
            error = e.error
        except Exception as e:  # 에이전트의 예기치 못한 예외도 FAILED로 기록한다
            error = AgentError(code="INTERNAL_ERROR", message=f"{type(e).__name__}: {e}", stage=agent, retryable=False)

        if error is not None:
            payload = {"error": error.model_dump(mode="json")}
            exec_status, run_status = "FAILED", "FAILED"
        else:
            payload = output.model_dump(mode="json")
            exec_status = "COMPLETED"
            done = agent == "agent3" and output.status in ("IMPACT_DRAFT_READY", "NOT_REQUIRED")
            run_status = "COMPLETED" if done else "RUNNING"

        self._commit(run_id, agent, result_id, upstream, exec_status, payload, run_status, revision)
        record = ExecutedAgent(agent_type=agent, result_id=result_id, execution_status=exec_status,
                               output_status=None if error else output.status)
        if error is None:
            review = self._review_after(agent, output, result_id)
            if review is not None:
                snap = self._get_state(run_id)
                self._request_review(snap, agent, result_id, review)
        return (record,), error

    def _call_agent(self, agent: Stage, agent_in, ri: RunInputs):
        if agent == "agent1":
            return self.agents.agent1.run(agent_in)
        if agent == "agent2":
            return apply_guards(self.agents.agent2.run(agent_in))
        if not agent_in.targets():                 # 산정 대상 없음 → ③ 호출 없이 NOT_REQUIRED
            return Agent3Output(context=agent_in.context, meta=Meta(), status="NOT_REQUIRED", estimates=[])
        return finalize_estimates(self.agents.agent3.run(agent_in), ri.rate_card_id)

    @staticmethod
    def _review_after(agent: Stage, output, result_id: str) -> Optional[str]:
        # 포트에는 WAITING_INPUT 전이가 없어 사용자 입력 필요도 검토 대기로 표현한다(Backend와 합의 필요)
        if output.status == "NEEDS_INPUT":
            return f"{agent} 사용자 입력 필요"
        if agent == "agent3" and output.status == "WAITING_REVIEW":
            return "agent3 검토 필요"
        return None

    # ------------------------------------------------------------------ Backend 호출(재시도 정책)
    def _get_state(self, run_id: str) -> RunStateSnapshot:
        for attempt in range(self._max):
            try:
                return self.state.get_state(run_id)
            except StatePersistenceError:
                if attempt == self._max - 1:
                    raise

    def _persist(self, call, req):
        """저장 실패는 같은 operation_id로 재시도한다(멱등)."""
        for attempt in range(self._max):
            try:
                return call(req)
            except StatePersistenceError:
                if attempt == self._max - 1:
                    raise

    def _start(self, snap: RunStateSnapshot, agent: Stage):
        req = StartAgentRequest(run_id=snap.run_id, agent_type=agent, expected_state_revision=snap.state_revision,
                                operation_id=self._new_op())
        try:
            return self._persist(self.state.start_agent, req)
        except StatePersistenceError:
            raise _Stop("START_FAILED", "start_agent 저장 실패가 반복됨")
        except (InvalidStateTransition, OperationConflict) as e:
            raise _Stop("START_REJECTED", str(e))

    def _commit(self, run_id, agent, result_id, upstream, exec_status, payload, run_status, revision):
        op = self._new_op()
        for _ in range(self._max):
            req = CommitResultRequest(
                run_id=run_id, expected_state_revision=revision, operation_id=op, agent_type=agent,
                result_id=result_id, execution_status=exec_status, upstream_result_ids=upstream,
                result_payload=payload, requested_run_status=run_status)
            try:
                return self._persist(self.state.commit_result, req)
            except StatePersistenceError:
                raise _Stop("COMMIT_FAILED", "commit 저장 실패가 반복됨 — Agent는 RUNNING으로 남는다")
            except (InvalidStateTransition, OperationConflict) as e:
                raise _Stop("COMMIT_REJECTED", str(e))
            except StateRevisionConflict:
                # 에이전트는 다시 실행하지 않는다. 이미 반영됐는지 확인하고, 아니면 최신 revision으로 재커밋한다.
                snap = self._get_state(run_id)
                if snap.agents[agent].current_result_id == result_id:
                    return None
                if snap.agents[agent].status != "RUNNING" or is_waiting_for_user(snap):
                    raise _Stop("COMMIT_REJECTED", "상태가 바뀌어 결과를 커밋할 수 없다")
                revision, op = snap.state_revision, self._new_op()
        raise _Stop("COMMIT_REJECTED", "revision 충돌이 반복됨")

    def _request_review(self, snap: RunStateSnapshot, agent: Stage, target: Optional[str], reason: str) -> None:
        revision, op = snap.state_revision, self._new_op()
        for _ in range(self._max):
            req = RequestReviewRequest(run_id=snap.run_id, expected_state_revision=revision, operation_id=op,
                                       agent_type=agent, target_result_id=target, reason=reason)
            try:
                self._persist(self.state.request_review, req)
                return
            except StatePersistenceError:
                raise _Stop("COMMIT_FAILED", "request_review 저장 실패가 반복됨")
            except (InvalidStateTransition, OperationConflict) as e:
                raise _Stop("COMMIT_REJECTED", str(e))
            except StateRevisionConflict:
                latest = self._get_state(snap.run_id)
                if latest.pending_review is not None:
                    return
                revision, op = latest.state_revision, self._new_op()
        raise _Stop("STATE_CHANGED", "request_review revision 충돌이 반복됨")

    @staticmethod
    def _result(snap: RunStateSnapshot, outcome: Outcome, executed, reason=None, error=None, detail=None) -> RuntimeResult:
        return RuntimeResult(run_id=snap.run_id, outcome=outcome, executed=executed, run_status=snap.status,
                             state_revision=snap.state_revision, pending_review=snap.pending_review,
                             stop_reason=reason, error=error, detail=detail or None)
