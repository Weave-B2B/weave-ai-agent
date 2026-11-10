"""RunStateClient의 테스트용 메모리 구현. Backend 계약의 기대 동작을 고정하는 용도이며 운영용이 아니다."""
import hashlib
import threading
from datetime import datetime, timezone

from app.orchestration.run_state import (
    AGENT_PREREQUISITES, AGENT_STAGES, AgentExecutionStatus, AgentState, CommitResultRequest, CommittedResult,
    InvalidStateTransition, OperationConflict, PendingReview, RecordEditRequest, RequestReviewRequest,
    ResolveReviewRequest, ResultNotFound, RunNotFound, RunStateSnapshot, StartAgentRequest, StateChangeReceipt, StatePersistenceError,
    StateRevisionConflict,
)
from schema.schemas import RunStatus, Stage

_ALLOWED: dict[RunStatus, set[RunStatus]] = {
    "QUEUED": {"RUNNING", "FAILED"},
    "RUNNING": {"RUNNING", "WAITING_INPUT", "WAITING_PM_REVIEW", "COMPLETED", "FAILED"},
    "WAITING_INPUT": {"RUNNING", "FAILED"},
    "WAITING_PM_REVIEW": {"RUNNING", "FAILED"},
    "COMPLETED": {"RUNNING"},
    "FAILED": {"RUNNING"},
}


class _Run:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.revision = 0
        self.status: RunStatus = "QUEUED"
        self.agents: dict[Stage, AgentState] = {a: AgentState() for a in AGENT_STAGES}
        self.result_counts: dict[str, int] = {}
        self.pending: PendingReview | None = None
        self.history: list[dict] = []
        self.results: dict[str, CommittedResult] = {}
        self.receipts: dict[str, tuple[str, StateChangeReceipt]] = {}


class FakeRunStateClient:
    """Backend 계약 모사. RunStateClient 메서드(get_state/get_result/start_agent/commit_result/record_edit/
    request_review)만 Runtime이 사용한다. create_run, fail_next_write, set_agent_status, simulate_* 는 테스트 전용 제어다."""

    def __init__(self, strict_commit: bool = False):
        # True면 실제 Backend와 같은 commit 검증(RUNNING 필수, result_id 재사용 금지, upstream 일치)을 적용한다
        self._strict_commit = strict_commit
        self._runs: dict[str, _Run] = {}
        self._lock = threading.RLock()
        self._fail_only: type | None | bool = False
        self._bump_only: type | None | bool = False

    def create_run(self, run_id: str) -> None:
        with self._lock:
            self._runs[run_id] = _Run(run_id)

    def fail_next_write(self, only: type | None = None) -> None:
        """다음 쓰기 한 번(only 지정 시 해당 요청 타입)을 저장 실패로 만든다(상태는 변하지 않는다)."""
        self._fail_only = only

    def simulate_concurrent_write(self, only: type | None = None) -> None:
        """다음 쓰기 직전에 다른 주체가 revision을 올린 것처럼 만든다."""
        self._bump_only = only

    def simulate_stale(self, run_id: str, agent_type: Stage) -> None:
        with self._lock:
            run = self._get(run_id)
            run.agents[agent_type] = run.agents[agent_type].model_copy(update={"stale": True})

    def simulate_review_resolved(self, run_id: str) -> None:
        """Backend 쪽 사용자 검토 완료를 모사한다. AI 서버가 호출하는 계약이 아니다."""
        with self._lock:
            run = self._get(run_id)
            run.pending, run.status = None, "RUNNING"
            run.revision += 1
            run.history.append({"event": "review_resolved"})

    def set_agent_status(self, run_id: str, agent_type: Stage, status: AgentExecutionStatus) -> None:
        """테스트 전용. RUNNING/SKIPPED로 바꾸는 정식 계약 메서드는 Backend와 합의 전이라 아직 없다."""
        with self._lock:
            run = self._get(run_id)
            run.agents[agent_type] = run.agents[agent_type].model_copy(update={"status": status})

    def history(self, run_id: str) -> list[dict]:
        return list(self._get(run_id).history)

    def get_state(self, run_id: str) -> RunStateSnapshot:
        with self._lock:
            return self._snapshot(self._get(run_id))

    def get_result(self, run_id: str, result_id: str) -> CommittedResult:
        with self._lock:
            run = self._get(run_id)
            if result_id not in run.results:
                raise ResultNotFound(f"result를 찾을 수 없습니다: {result_id}", run_id=run_id)
            stored = run.results[result_id]
            current = run.agents[stored.agent_type].current_result_id == result_id
            return stored.model_copy(update={"is_current": current})

    def start_agent(self, req: StartAgentRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            if run.status in ("WAITING_INPUT", "WAITING_PM_REVIEW") or run.pending is not None:
                raise InvalidStateTransition("사용자 검토/입력 대기 중에는 실행을 시작할 수 없습니다", run_id=run.run_id)
            if run.agents[req.agent_type].status == "RUNNING":
                raise InvalidStateTransition(f"{req.agent_type}는 이미 실행 중입니다", run_id=run.run_id)
            for pre in AGENT_PREREQUISITES[req.agent_type]:
                dep = run.agents[pre]
                if dep.status != "COMPLETED" or dep.stale:
                    raise InvalidStateTransition(f"선행 결과 {pre}가 없거나 stale입니다", run_id=run.run_id)
            self._check_transition(run, "RUNNING")
            run.agents[req.agent_type] = run.agents[req.agent_type].model_copy(update={"status": "RUNNING"})
            run.status = "RUNNING"
            run.history.append({"event": "agent_started", "agent_type": req.agent_type,
                                "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def commit_result(self, req: CommitResultRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            # Backend와 같은 규칙: 실행 승인(RUNNING)된 Agent만 커밋, result_id 재사용 금지, upstream은 선행 현재 결과와 일치
            if self._strict_commit and run.agents[req.agent_type].status != "RUNNING":
                raise InvalidStateTransition(f"{req.agent_type}가 실행 중이 아니어서 결과를 커밋할 수 없습니다",
                                             run_id=run.run_id)
            if self._strict_commit and req.result_id in run.results:
                raise InvalidStateTransition(f"이미 사용된 result_id입니다: {req.result_id}", run_id=run.run_id)
            if self._strict_commit and req.execution_status == "COMPLETED":
                expected = [run.agents[p].current_result_id for p in AGENT_PREREQUISITES[req.agent_type]]
                if list(req.upstream_result_ids) != expected:
                    raise InvalidStateTransition(
                        f"upstream_result_ids가 선행 Agent의 현재 결과와 다릅니다: expected={expected}", run_id=run.run_id)
            self._check_transition(run, req.requested_run_status)
            n = run.result_counts.get(req.agent_type, 0) + 1
            run.result_counts[req.agent_type] = n
            prev = run.agents[req.agent_type]
            if req.execution_status == "COMPLETED":
                run.agents[req.agent_type] = AgentState(
                    status="COMPLETED", current_result_id=req.result_id, result_revision=n,
                    upstream_result_ids=req.upstream_result_ids)
            else:
                run.agents[req.agent_type] = prev.model_copy(update={"status": "FAILED"})
            run.results[req.result_id] = CommittedResult(
                run_id=run.run_id, agent_type=req.agent_type, result_id=req.result_id, result_revision=n,
                execution_status=req.execution_status, upstream_result_ids=req.upstream_result_ids,
                result_payload=req.result_payload, created_at=datetime.now(timezone.utc), is_current=False)
            if req.execution_status == "COMPLETED":
                self._mark_downstream_stale(run, req.agent_type)
            run.status = "FAILED" if req.execution_status == "FAILED" else req.requested_run_status
            run.pending = None
            run.history.append({"event": "result_committed", "agent_type": req.agent_type,
                                "result_id": req.result_id, "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id, req.result_id, n)
        return self._write(req, apply)

    def record_edit(self, req: RecordEditRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            agent = run.agents[req.agent_type]
            if agent.current_result_id != req.target_result_id:
                raise InvalidStateTransition("수정 대상이 현재 결과가 아닙니다", run_id=run.run_id)
            if agent.status == "RUNNING":
                raise InvalidStateTransition(f"{req.agent_type}가 실행 중이라 수정할 수 없습니다", run_id=run.run_id)
            if req.new_result_id in run.results:
                raise InvalidStateTransition(f"이미 사용된 result_id입니다: {req.new_result_id}", run_id=run.run_id)
            n = run.result_counts.get(req.agent_type, 0) + 1
            run.result_counts[req.agent_type] = n
            upstream = run.results[req.target_result_id].upstream_result_ids
            run.results[req.new_result_id] = CommittedResult(
                run_id=run.run_id, agent_type=req.agent_type, result_id=req.new_result_id, result_revision=n,
                execution_status="COMPLETED", upstream_result_ids=upstream, result_payload=req.edited_payload,
                created_at=datetime.now(timezone.utc), is_current=False)
            run.agents[req.agent_type] = agent.model_copy(update={
                "current_result_id": req.new_result_id, "result_revision": n, "stale": False})
            self._mark_downstream_stale(run, req.agent_type)
            run.history.append({"event": "user_edit", "agent_type": req.agent_type,
                                "target_result_id": req.target_result_id, "result_id": req.new_result_id,
                                "edited_by": req.edited_by, "reason": req.reason, "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id, req.new_result_id, n)
        return self._write(req, apply)

    def request_review(self, req: RequestReviewRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            if run.status != "RUNNING":
                raise InvalidStateTransition(f"{run.status} 상태에서는 검토 대기로 전환할 수 없습니다", run_id=run.run_id)
            run.status = "WAITING_INPUT" if req.kind == "INPUT" else "WAITING_PM_REVIEW"
            run.pending = PendingReview(agent_type=req.agent_type, target_result_id=req.target_result_id,
                                        reason=req.reason, kind=req.kind)
            run.history.append({"event": "review_requested", "agent_type": req.agent_type,
                                "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def resolve_review(self, req: ResolveReviewRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            if run.pending is None or run.status not in ("WAITING_INPUT", "WAITING_PM_REVIEW"):
                raise InvalidStateTransition("해제할 검토/입력 대기가 없습니다", run_id=run.run_id)
            self._check_transition(run, "RUNNING")
            if req.requested_run_status == "COMPLETED":
                if req.decision == "REJECTED":
                    raise InvalidStateTransition("REJECTED 결정으로 Run을 COMPLETED로 해제할 수 없습니다", run_id=run.run_id)
                if any(a.status != "COMPLETED" or a.stale for a in run.agents.values()):
                    raise InvalidStateTransition("모든 Agent가 최신 COMPLETED가 아니면 COMPLETED로 해제할 수 없습니다",
                                                 run_id=run.run_id)
            run.history.append({"event": "review_resolved", "agent_type": run.pending.agent_type,
                                "kind": run.pending.kind, "decision": req.decision, "resolved_by": req.resolved_by,
                                "comment": req.comment, "operation_id": req.operation_id})
            run.status = req.requested_run_status
            run.pending = None
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def _get(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFound(f"run을 찾을 수 없습니다: {run_id}", run_id=run_id)
        return run

    def _write(self, req, apply) -> StateChangeReceipt:
        with self._lock:
            run = self._get(req.run_id)
            digest = hashlib.sha256(
                f"{type(req).__name__}:{req.model_dump_json(exclude={'expected_state_revision'})}".encode()
            ).hexdigest()
            prior = run.receipts.get(req.operation_id)
            if prior is not None:
                if prior[0] != digest:
                    raise OperationConflict("같은 operation_id가 다른 요청에 사용되었습니다", run_id=run.run_id)
                return prior[1].model_copy(update={"replayed": True})
            if self._bump_only is not False and (self._bump_only is None or isinstance(req, self._bump_only)):
                self._bump_only = False
                run.revision += 1
                run.history.append({"event": "concurrent_write"})
            if req.expected_state_revision != run.revision:
                raise StateRevisionConflict(
                    "state_revision이 일치하지 않습니다", run_id=run.run_id,
                    expected_revision=req.expected_state_revision, actual_revision=run.revision)
            if self._fail_only is not False and (self._fail_only is None or isinstance(req, self._fail_only)):
                self._fail_only = False
                raise StatePersistenceError("저장 실패", run_id=run.run_id)
            receipt = apply(run)
            run.receipts[req.operation_id] = (digest, receipt)
            return receipt

    @staticmethod
    def _mark_downstream_stale(run: _Run, agent_type: Stage) -> None:
        # 결과가 있는 하위 Agent만 stale로 표시한다. 결과는 지우지 않는다.
        for other, pres in AGENT_PREREQUISITES.items():
            state = run.agents[other]
            if agent_type in pres and state.current_result_id is not None:
                run.agents[other] = state.model_copy(update={"stale": True})

    @staticmethod
    def _check_transition(run: _Run, target: RunStatus) -> None:
        if target not in _ALLOWED[run.status]:
            raise InvalidStateTransition(f"{run.status} -> {target} 전환은 허용되지 않습니다", run_id=run.run_id)

    def _receipt(self, run: _Run, operation_id: str, result_id=None, result_revision=None) -> StateChangeReceipt:
        run.revision += 1
        return StateChangeReceipt(run_id=run.run_id, operation_id=operation_id, state_revision=run.revision,
                                  status=run.status, result_id=result_id, result_revision=result_revision)

    @staticmethod
    def _snapshot(run: _Run) -> RunStateSnapshot:
        return RunStateSnapshot(run_id=run.run_id, state_revision=run.revision, status=run.status,
                                agents={k: v.model_copy() for k, v in run.agents.items()}, pending_review=run.pending)
