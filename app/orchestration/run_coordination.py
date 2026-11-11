"""Proposed, opt-in Run coordination contract; not the deployed Backend API.

User answers and trusted orchestration writes are deliberately different DTOs.
Existing /api/v2/runs requests and schema/schemas.py remain compatible.
"""
import hashlib
import json
from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, model_validator

from app.orchestration.run_state import (
    CommitResultRequest, RunStateSnapshot, Stage, StartAgentRequest, StateChangeReceipt,
)
from schema.schemas import Answer, _M


def payload_fingerprint(payload: dict) -> str:
    """Only execution metadata may change without invalidating a result.

    Questions, evidence, input context and document contents remain significant.
    This intentionally errs towards a new result rather than a false no-op.
    """
    value = {k: v for k, v in payload.items() if k != "meta"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class RunWrite(_M):
    run_id: str
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)


class AcquireExecution(StartAgentRequest):
    lease_seconds: int = Field(default=600, ge=30, le=1800)
    recover_expired: bool = False


class ExecutionLease(_M):
    token: str
    expires_at: datetime
    upstream_result_ids: list[str]


class ExecutionReceipt(StateChangeReceipt):
    execution_token: str


class FencedCommit(CommitResultRequest):
    execution_token: str = Field(min_length=1)


class FinishUnchanged(RunWrite):
    agent_type: Stage
    execution_token: str = Field(min_length=1)
    target_result_id: str
    observed_payload: dict


class PMQuestion(_M):
    question_id: str = Field(min_length=1)
    item_ids: list[str] = Field(default_factory=list, description="Empty means a request-wide question")
    field: str = Field(min_length=1)
    text: str = Field(min_length=1)
    choices: list[str] = Field(default_factory=list, max_length=8)
    allow_other: bool = True
    blocking: bool = True
    status: Literal["open", "resolved"] = "open"
    answer: str | None = None
    stage: Stage = "agent2"


class CoordinationState(_M):
    contract_version: Literal["run-coordination/1"] = "run-coordination/1"
    questions: list[PMQuestion] = Field(default_factory=list)
    quote_links: dict[str, list[str]] = Field(default_factory=dict)
    quote_source_result_id: str | None = None
    research_attempts: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = Field(default=0, ge=0, le=80)
    segment_llm_calls: int = Field(default=0, ge=0, le=40)
    segment_steps: int = Field(default=0, ge=0, le=12)
    answers: list[Answer] = Field(default_factory=list)
    hold_reason: str | None = None

    @model_validator(mode="after")
    def distinct_questions(self):
        ids = [q.question_id for q in self.questions]
        keys = [(tuple(sorted(q.item_ids)), q.field.strip().casefold()) for q in self.questions]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise ValueError("question IDs and item/field keys must be unique")
        if any(n < 0 for n in self.research_attempts.values()):
            raise ValueError("research attempts cannot be negative")
        return self


class SaveCoordination(RunWrite):
    """Trusted host only. Never use this as the public user-input request body."""
    state: CoordinationState


class AnswerQuestion(RunWrite):
    question_id: str
    answer: str = Field(min_length=1, max_length=4000)
    answered_by: str = Field(min_length=1)
    disposition: Literal["provided", "unknown"] = "provided"


class ReserveLlmAttempt(RunWrite):
    pass


class ResumeRun(RunWrite):
    resumed_by: str = Field(min_length=1)


class ReviewBundle(_M):
    result_ids: dict[Stage, str]
    document_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class RequestFinalReview(RunWrite):
    bundle: ReviewBundle


class FinalReviewDecision(RunWrite):
    bundle: ReviewBundle
    reviewed_by: str = Field(min_length=1)
    decision: Literal["approve", "hold", "reject"]


class CoordinatedRun(_M):
    state: RunStateSnapshot
    coordination: CoordinationState
    executions: dict[Stage, ExecutionLease] = Field(default_factory=dict)
    review_bundle: ReviewBundle | None = None


class CoordinatedRunClient(Protocol):
    def get_inputs(self, run_id: str): ...
    def get_state(self, run_id: str) -> RunStateSnapshot: ...
    def get_result(self, run_id: str, result_id: str): ...
    def get_coordinated_run(self, run_id: str) -> CoordinatedRun: ...
    def acquire_execution(self, req: AcquireExecution) -> ExecutionReceipt: ...
    def commit_result(self, req: FencedCommit) -> StateChangeReceipt: ...
    def finish_unchanged(self, req: FinishUnchanged) -> StateChangeReceipt: ...
    def save_coordination(self, req: SaveCoordination) -> StateChangeReceipt: ...
    def answer_question(self, req: AnswerQuestion) -> StateChangeReceipt: ...
    def request_final_review(self, req: RequestFinalReview) -> StateChangeReceipt: ...
    def final_review(self, req: FinalReviewDecision) -> StateChangeReceipt: ...
    def reserve_llm_attempt(self, req: ReserveLlmAttempt) -> StateChangeReceipt: ...
    def resume_run(self, req: ResumeRun) -> StateChangeReceipt: ...
