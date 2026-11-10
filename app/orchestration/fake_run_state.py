"""RunStateClient의 테스트용 메모리 구현. Backend 계약의 기대 동작을 고정하는 용도이며 운영용이 아니다."""
import hashlib
import threading
from typing import Callable

from app.orchestration.run_state import (
    AGENT_STAGES, AgentExecutionStatus, AgentState, CommitResultRequest, InvalidStateTransition, OperationConflict,
    PendingReview, RecordEditRequest, RequestReviewRequest, RunNotFound, RunStateSnapshot, StateChangeReceipt,
    StatePersistenceError, StateRevisionConflict,
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
        self.receipts: dict[str, tuple[str, StateChangeReceipt]] = {}


class FakeRunStateClient:
    def __init__(self):
        self._runs: dict[str, _Run] = {}
        self._lock = threading.RLock()
        self._fail: Callable[[], bool] | None = None

    def create_run(self, run_id: str) -> None:
        with self._lock:
            self._runs[run_id] = _Run(run_id)

    def fail_next_write(self) -> None:
        """다음 쓰기 한 번을 저장 실패로 만든다(상태는 변하지 않는다)."""
        self._fail = lambda: True

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

    def commit_result(self, req: CommitResultRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
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
            run.status = "FAILED" if req.execution_status == "FAILED" else req.requested_run_status
            run.pending = None
            run.history.append({"event": "result_committed", "agent_type": req.agent_type,
                                "result_id": req.result_id, "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id, req.result_id, n)
        return self._write(req, apply)

    def record_edit(self, req: RecordEditRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            if run.agents[req.agent_type].current_result_id != req.target_result_id:
                raise InvalidStateTransition("수정 대상이 현재 결과가 아닙니다", run_id=run.run_id)
            run.history.append({"event": "user_edit", "agent_type": req.agent_type,
                                "target_result_id": req.target_result_id, "edited_by": req.edited_by,
                                "changes": req.changes, "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def request_review(self, req: RequestReviewRequest) -> StateChangeReceipt:
        def apply(run: _Run) -> StateChangeReceipt:
            if run.status != "RUNNING":
                raise InvalidStateTransition(f"{run.status} 상태에서는 검토 대기로 전환할 수 없습니다", run_id=run.run_id)
            run.status = "WAITING_PM_REVIEW"
            run.pending = PendingReview(agent_type=req.agent_type, target_result_id=req.target_result_id,
                                        reason=req.reason)
            run.history.append({"event": "review_requested", "agent_type": req.agent_type,
                                "operation_id": req.operation_id})
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
            if req.expected_state_revision != run.revision:
                raise StateRevisionConflict(
                    "state_revision이 일치하지 않습니다", run_id=run.run_id,
                    expected_revision=req.expected_state_revision, actual_revision=run.revision)
            if self._fail is not None:
                self._fail = None
                raise StatePersistenceError("저장 실패", run_id=run.run_id)
            receipt = apply(run)
            run.receipts[req.operation_id] = (digest, receipt)
            return receipt

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
