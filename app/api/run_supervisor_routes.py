"""Opt-in Run-backed orchestrator APIs, separate from the legacy prototype API."""
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.run_routes import actor_member_id
from app.agents.base import AgentFailure
from app.orchestration.run_coordination import (
    AnswerQuestion, FinalReviewDecision, PMQuestion, ReviewBundle,
    ResumeRun,
)
from app.orchestration.run_state import RunStateSnapshot
from app.orchestration.services import RunSupervisorServices, build_run_supervisor_services

router = APIRouter(prefix="/api/v2/runs/{run_id}/supervisor", tags=["Run supervisor"],
                   dependencies=[Depends(actor_member_id)])


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StepBody(RequestBody):
    expected_state_revision: int = Field(ge=0)


class AnswerBody(StepBody):
    operation_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)
    disposition: Literal["provided", "unknown"] = "provided"


class ReviewBody(StepBody):
    operation_id: str = Field(min_length=1)
    bundle: ReviewBundle
    decision: Literal["approve", "hold", "reject"]


class ResumeBody(StepBody):
    operation_id: str = Field(min_length=1)


class SupervisorView(BaseModel):
    status: str
    action: str | None = None
    reason: str | None = None
    state: RunStateSnapshot
    questions: list[PMQuestion]
    documents: dict
    review_bundle: ReviewBundle | None = None


def get_services(actor: str = Depends(actor_member_id)):
    yield from build_run_supervisor_services(actor)


def view(backend, run_id, *, status="ready", action=None, reason=None):
    run = backend.get_coordinated_run(run_id)
    result = run.state.agents["agent3"]
    documents = {}
    if status == "ready" and run.coordination.hold_reason:
        status, reason = "waiting", run.coordination.hold_reason
    if result.current_result_id and not result.stale:
        documents = backend.get_result(run_id, result.current_result_id).result_payload.get("documents", {})
    # Do not expose execution lease tokens or internal host state to a browser.
    return SupervisorView(status=status, action=action, reason=reason, state=run.state,
                          questions=run.coordination.questions, documents=documents, review_bundle=run.review_bundle)


@router.get("", response_model=SupervisorView)
def get_supervisor(run_id: str, svc: RunSupervisorServices = Depends(get_services)):
    state = svc.backend.get_state(run_id)
    status = {"COMPLETED": "completed", "WAITING_INPUT": "waiting_input",
              "WAITING_PM_REVIEW": "waiting_final_review", "FAILED": "error"}.get(state.status, "ready")
    if any(agent.status == "RUNNING" for agent in state.agents.values()):
        status = "running"
    return view(svc.backend, run_id, status=status)


@router.post("/step", response_model=SupervisorView)
def step(run_id: str, body: StepBody, svc: RunSupervisorServices = Depends(get_services)):
    try:
        outcome = svc.runtime.step(run_id, expected_revision=body.expected_state_revision)
    except AgentFailure as exc:
        return JSONResponse(status_code=502, content={"code": exc.error.code,
            "message": exc.error.message, "retryable": exc.error.retryable})
    return view(svc.backend, run_id, status=outcome["status"], action=outcome.get("action"), reason=outcome.get("reason"))


@router.post("/execute", response_model=SupervisorView)
def execute(run_id: str, body: StepBody, svc: RunSupervisorServices = Depends(get_services)):
    try:
        outcome = svc.runtime.run(run_id, expected_revision=body.expected_state_revision)
    except AgentFailure as exc:
        return JSONResponse(status_code=502, content={"code": exc.error.code,
            "message": exc.error.message, "retryable": exc.error.retryable})
    return view(svc.backend, run_id, status=outcome["status"], reason=outcome.get("reason"))


@router.post("/answers", response_model=SupervisorView)
def answer(run_id: str, body: AnswerBody, actor: str = Depends(actor_member_id),
           svc: RunSupervisorServices = Depends(get_services)):
    svc.runtime.answer(AnswerQuestion(run_id=run_id, answered_by=actor, **body.model_dump()))
    return get_supervisor(run_id, svc)


@router.post("/final-review", response_model=SupervisorView)
def review(run_id: str, body: ReviewBody, actor: str = Depends(actor_member_id),
           svc: RunSupervisorServices = Depends(get_services)):
    svc.runtime.review(FinalReviewDecision(run_id=run_id, reviewed_by=actor, **body.model_dump()))
    return get_supervisor(run_id, svc)


@router.post("/resume", response_model=SupervisorView)
def resume(run_id: str, body: ResumeBody, actor: str = Depends(actor_member_id),
           svc: RunSupervisorServices = Depends(get_services)):
    svc.backend.resume_run(ResumeRun(run_id=run_id, resumed_by=actor, **body.model_dump()))
    return get_supervisor(run_id, svc)
