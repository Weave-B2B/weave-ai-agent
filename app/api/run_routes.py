"""Run 단위 자동 실행(오케스트레이션) API. 상태·결과의 소유자는 Backend이며, 이 API는 Runtime 호출과 Backend 입력 제출을 중계한다.

행위자는 X-Actor-Member-Id 헤더로 받고 Backend 호출에 그대로 전달한다(권한 검증은 Backend가 수행).
"""
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from schema.schemas import Agent1Input, PMReview, Stage

from app.orchestration.http_run_state import RunAccessDenied, RunRequestRejected
from app.orchestration.run_state import (
    CommittedResult, InvalidEditPayload, InvalidStateTransition, OperationConflict, ReviewDecision, RunInputs, RunNotFound, RunStateError, RunStateSnapshot,
    StateChangeReceipt, StatePersistenceError, StateRevisionConflict, SubmitInputRequest,
)
from app.orchestration.runtime import RuntimeResult
from app.orchestration.services import BackendNotConfigured, RunServices, build_run_services

router = APIRouter(prefix="/api/v2/runs", tags=["Run Orchestration"])


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CreateRunBody(_Req):
    project_id: str = Field(min_length=1)
    change_request_id: str = Field(min_length=1)
    agent1_input: Agent1Input
    rate_card_id: str = Field(min_length=1)


class SubmitPmReviewBody(_Req):
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    payload: PMReview


class ResumeBody(_Req):
    decision: ReviewDecision
    comment: Optional[str] = None


class EditResultBody(_Req):
    target_result_id: str = Field(min_length=1)
    expected_state_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1)
    edited_payload: dict
    reason: Optional[str] = None


def actor_member_id(x_actor_member_id: str = Header(min_length=1)) -> str:
    return x_actor_member_id


def get_services(actor: str = Depends(actor_member_id)) -> Iterator[RunServices]:
    # 테스트는 app.dependency_overrides[get_services]로 교체한다(행위자 헤더 검사는 유지된다).
    yield from build_run_services(actor)


_STATUS = [
    (RunNotFound, 404), (StateRevisionConflict, 409), (OperationConflict, 409), (InvalidStateTransition, 409),
    (RunAccessDenied, 403), (RunRequestRejected, 422), (InvalidEditPayload, 422), (StatePersistenceError, 503),
]


def _error_body(exc: RunStateError) -> dict:
    body = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
    if isinstance(exc, StateRevisionConflict):
        body["expected_state_revision"], body["actual_state_revision"] = exc.expected_revision, exc.actual_revision
    return body


def register_error_handlers(app) -> None:
    @app.exception_handler(RunStateError)
    async def _run_state_error(_: Request, exc: RunStateError):
        status = next((s for t, s in _STATUS if isinstance(exc, t)), 502)
        return JSONResponse(_error_body(exc), status_code=status)

    @app.exception_handler(BackendNotConfigured)
    async def _not_configured(_: Request, exc: BackendNotConfigured):
        return JSONResponse({"code": "BACKEND_NOT_CONFIGURED", "message": str(exc), "retryable": False}, status_code=503)


@router.post("", response_model=RunStateSnapshot, status_code=201)
def create_run(body: CreateRunBody, svc: RunServices = Depends(get_services), _: str = Depends(actor_member_id)) -> RunStateSnapshot:
    inputs = RunInputs(agent1_input=body.agent1_input, rate_card_id=body.rate_card_id)
    return svc.backend.create_run(body.project_id, body.change_request_id, inputs)


@router.get("/{run_id}", response_model=RunStateSnapshot)
def get_run(run_id: str, svc: RunServices = Depends(get_services), _: str = Depends(actor_member_id)) -> RunStateSnapshot:
    return svc.backend.get_state(run_id)


@router.post("/{run_id}/orchestrations", response_model=RuntimeResult)
def orchestrate(run_id: str, svc: RunServices = Depends(get_services), _: str = Depends(actor_member_id)) -> RuntimeResult:
    """규칙 기반 Runtime이 더 진행할 수 없을 때까지(완료·검토 대기·실패·중단) 실행한다. 동기 호출이다."""
    return svc.runtime.run(run_id)


@router.post("/{run_id}/pm-review", response_model=StateChangeReceipt)
def submit_pm_review(run_id: str, body: SubmitPmReviewBody, actor: str = Depends(actor_member_id),
                     svc: RunServices = Depends(get_services)) -> StateChangeReceipt:
    return svc.backend.submit_input(SubmitInputRequest(
        run_id=run_id, expected_state_revision=body.expected_state_revision, operation_id=body.operation_id,
        submitted_by=actor, payload=body.payload))


@router.post("/{run_id}/resume", response_model=RuntimeResult)
def resume(run_id: str, body: ResumeBody, actor: str = Depends(actor_member_id),
           svc: RunServices = Depends(get_services)) -> RuntimeResult:
    return svc.runtime.resume(run_id, actor, body.decision, body.comment)


# ---- 직접 실행: 사용자가 Agent 하나를 지정해 실행·수정한다(자동 실행 화면 → 직접 실행 화면 연결용)
@router.post("/{run_id}/agents/{agent}/executions", response_model=RuntimeResult)
def execute_agent(run_id: str, agent: Stage, svc: RunServices = Depends(get_services),
                  _: str = Depends(actor_member_id)) -> RuntimeResult:
    """지정한 Agent만 실행한다(재실행 포함, 동기). 다음 Agent로 이어가지 않는다."""
    return svc.runtime.run_agent(run_id, agent)


@router.post("/{run_id}/agents/{agent}/edits", response_model=StateChangeReceipt)
def edit_agent_result(run_id: str, agent: Stage, body: EditResultBody, actor: str = Depends(actor_member_id),
                      svc: RunServices = Depends(get_services)) -> StateChangeReceipt:
    """현재 결과를 수정한다. 덮어쓰지 않고 새 result revision을 커밋하며 하위 결과는 stale이 된다."""
    return svc.runtime.edit_result(run_id, agent, body.target_result_id, body.edited_payload, actor, body.expected_state_revision,
                                   body.operation_id, body.reason)


@router.get("/{run_id}/results/{result_id}", response_model=CommittedResult)
def get_result(run_id: str, result_id: str, svc: RunServices = Depends(get_services),
               _: str = Depends(actor_member_id)) -> CommittedResult:
    return svc.backend.get_result(run_id, result_id)
