"""Backend가 소유하는 Run 상태에 대한 AI 서버 측 계약(port).

AI 서버는 Run 상태의 최종 영속 소유자가 아니다. 모든 변경은 expected_state_revision과
operation_id를 붙여 Backend에 요청하고, Backend가 돌려준 상태·revision만 기준으로 삼는다.
"""
from datetime import datetime
from typing import Literal, Optional, Protocol

from pydantic import ConfigDict, Field, model_validator

from schema.schemas import Agent1Input, PMReview, RunStatus, Stage, _M

ActorType = Literal["user", "agent", "system"]
ResultExecutionStatus = Literal["COMPLETED", "FAILED"]
ReviewKind = Literal["PM_REVIEW", "INPUT", "FINAL_REVIEW"]
ReviewDecision = Literal["APPROVED", "REJECTED"]


class _Resp(_M):
    """Backend 응답 DTO. 호환 정책: 응답은 모르는 필드를 무시해 Backend의 필드 추가에 깨지지 않게 하고,
    요청 DTO(_M)는 엄격하게 검증한다."""
    model_config = ConfigDict(extra="ignore")


class RunInputs(_M):
    """Run의 입력 문서 묶음. Backend run_input에 저장된다(REQUEST_INPUT은 Run 생성 시 고정, pm_review는 제출 시 추가)."""
    agent1_input: Agent1Input
    rate_card_id: str
    pm_review: Optional[PMReview] = None


class RunInputsView(_Resp):
    """GET /inputs 응답. 모르는 필드는 무시한다."""
    run_id: str
    agent1_input: Agent1Input
    rate_card_id: str
    pm_review: Optional[PMReview] = None
    pm_review_revision: Optional[int] = None

    def to_inputs(self) -> RunInputs:
        return RunInputs(agent1_input=self.agent1_input, rate_card_id=self.rate_card_id, pm_review=self.pm_review)


class RunStateError(Exception):
    """retryable=True 인 오류만 같은 요청(같은 operation_id)을 그대로 재시도할 수 있다."""
    code = "RUN_STATE_ERROR"
    retryable = False

    def __init__(self, message: str = "", *, run_id: str | None = None):
        super().__init__(message or self.code)
        self.run_id = run_id


class RunNotFound(RunStateError):
    code = "RUN_NOT_FOUND"


class RunInputNotFound(RunNotFound):
    """Run은 있으나 최초 입력이 저장되어 있지 않다. 재시도해도 같다."""
    code = "RUN_INPUT_NOT_FOUND"


class ResultNotFound(RunNotFound):
    code = "RESULT_NOT_FOUND"


class StateRevisionConflict(RunStateError):
    """재시도 금지: 상태를 다시 읽고 계획을 재평가한 뒤 새 operation_id로 요청해야 한다."""
    code = "STATE_REVISION_CONFLICT"

    def __init__(self, message: str = "", *, run_id: str | None = None,
                 expected_revision: int | None = None, actual_revision: int | None = None):
        super().__init__(message, run_id=run_id)
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class OperationConflict(RunStateError):
    """같은 operation_id가 다른 내용으로 다시 사용됨."""
    code = "OPERATION_CONFLICT"


class InvalidStateTransition(RunStateError):
    code = "INVALID_STATE_TRANSITION"


class InvalidEditPayload(RunStateError):
    """사용자 수정 payload가 해당 Agent 출력 스키마와 맞지 않는다. 고치지 않으면 재시도해도 같다."""
    code = "INVALID_EDIT_PAYLOAD"


class StatePersistenceError(RunStateError):
    """Backend 저장 실패. 같은 operation_id로 재시도해도 중복 반영되지 않는다."""
    code = "STATE_PERSISTENCE_ERROR"
    retryable = True


AgentExecutionStatus = Literal["NOT_STARTED", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]
AGENT_STAGES: tuple[Stage, ...] = ("agent1", "agent2", "agent3")
# Backend가 start_agent에서 강제하고 Runtime이 사전 검사에 쓰는 선행 결과 규칙
AGENT_PREREQUISITES: dict[Stage, tuple[Stage, ...]] = {
    "agent1": (), "agent2": ("agent1",), "agent3": ("agent1", "agent2"),
}


class AgentState(_Resp):
    """Agent 한 개의 실행 상태. Run 전체 상태(RunStatus)와 별개이며 사용자 검토 대기는 담지 않는다.

    current_result_id/result_revision은 마지막으로 성공 커밋된 결과를 가리킨다.
    실패한 시도는 이 포인터를 옮기지 않는다.
    """
    status: AgentExecutionStatus = "NOT_STARTED"
    current_result_id: Optional[str] = None
    result_revision: Optional[int] = Field(None, ge=1)
    upstream_result_ids: list[str] = []
    stale: bool = False

    @model_validator(mode="after")
    def _check_pointer(self):
        has_pointer = self.current_result_id is not None
        if has_pointer != (self.result_revision is not None):
            raise ValueError("current_result_id와 result_revision은 함께 있어야 합니다")
        if self.status == "COMPLETED" and not has_pointer:
            raise ValueError("COMPLETED 상태에는 current_result_id가 필요합니다")
        return self


class PendingReview(_Resp):
    agent_type: Stage
    target_result_id: Optional[str] = None
    reason: str
    kind: ReviewKind = "PM_REVIEW"


class RunStateSnapshot(_Resp):
    run_id: str
    state_revision: int = Field(ge=0)
    status: RunStatus
    agents: dict[Stage, AgentState]
    pending_review: Optional[PendingReview] = None

    @model_validator(mode="after")
    def _all_agents_present(self):
        if set(self.agents) != set(AGENT_STAGES):
            raise ValueError("agents에는 agent1·agent2·agent3가 모두 있어야 합니다")
        return self


class StartAgentRequest(_M):
    """Agent 실행 시작 승인 요청. Backend가 승인(RUNNING 전이)한 경우에만 Agent를 실행할 수 있다."""
    run_id: str
    agent_type: Stage
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)


class CommittedResult(_Resp):
    run_id: str
    agent_type: Stage
    result_id: str
    result_revision: int = Field(ge=1)
    execution_status: ResultExecutionStatus
    upstream_result_ids: list[str] = []
    result_payload: dict
    created_at: datetime
    is_current: bool = Field(description="조회 시점에 해당 Agent의 current_result_id인지 여부")


class CommitResultRequest(_M):
    run_id: str
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    agent_type: Stage
    result_id: str
    execution_status: ResultExecutionStatus = "COMPLETED"
    upstream_result_ids: list[str] = []
    result_payload: dict
    requested_run_status: RunStatus = "RUNNING"


class RecordEditRequest(_M):
    """사용자 수정은 대상 결과를 덮어쓰지 않고 새 result revision으로 커밋된다(응답 receipt에 새 result_id/revision).

    new_result_id는 AI 서버가 발급하고, 수정본은 대상 결과의 upstream_result_ids를 그대로 이어받는다.
    """
    run_id: str
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    agent_type: Stage
    target_result_id: str
    new_result_id: str = Field(min_length=1)
    edited_by: str
    edited_payload: dict
    reason: Optional[str] = None


class RequestReviewRequest(_M):
    run_id: str
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    agent_type: Stage
    target_result_id: Optional[str] = None
    reason: str
    kind: ReviewKind = "PM_REVIEW"


class ResolveReviewRequest(_M):
    """대기(WAITING_PM_REVIEW/WAITING_INPUT)를 해제한다. Run은 RUNNING으로 돌아가고 pending_review는 비워진다.

    다음에 무엇을 실행할지는 Runtime이 정한다(REJECTED여도 Backend는 실행 순서를 정하지 않는다).
    """
    run_id: str
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    resolved_by: str = Field(min_length=1)
    decision: ReviewDecision
    comment: Optional[str] = None
    # COMPLETED는 모든 Agent가 최신 COMPLETED이고 APPROVED일 때만 Backend가 허용한다(commit_result의 requested_run_status와 같은 의미)
    requested_run_status: Literal["RUNNING", "COMPLETED"] = "RUNNING"


class SubmitInputRequest(_M):
    """PM 검토 문서 제출. state_revision을 올리지만 Agent 상태·결과는 바꾸지 않는다. submitted_by는 행위자와 같아야 한다."""
    run_id: str
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    kind: Literal["PM_REVIEW"] = "PM_REVIEW"
    submitted_by: str = Field(min_length=1)
    payload: PMReview


class StateChangeReceipt(_Resp):
    """Backend가 확정한 변경 결과. result_revision은 Backend가 결과 저장 시 부여한다."""
    run_id: str
    operation_id: str
    state_revision: int = Field(ge=0)
    status: RunStatus
    result_id: Optional[str] = None
    result_revision: Optional[int] = Field(None, ge=1)
    replayed: bool = Field(False, description="같은 operation_id의 재요청이라 기존 결과를 돌려준 경우")


class RunStateClient(Protocol):
    """직접 실행과 자동 실행이 동일하게 사용하는 Run 상태 계약."""
    def get_state(self, run_id: str) -> RunStateSnapshot: ...
    def get_result(self, run_id: str, result_id: str) -> CommittedResult: ...
    def start_agent(self, req: StartAgentRequest) -> StateChangeReceipt: ...
    def commit_result(self, req: CommitResultRequest) -> StateChangeReceipt: ...
    def record_edit(self, req: RecordEditRequest) -> StateChangeReceipt: ...
    def request_review(self, req: RequestReviewRequest) -> StateChangeReceipt: ...
    def resolve_review(self, req: ResolveReviewRequest) -> StateChangeReceipt: ...
