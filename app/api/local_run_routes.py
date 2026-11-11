"""Explicit loopback prototype bridge to the common Run runtime and local SQLite.

The response adapter preserves existing per-agent screens; no Backend persistence
or authenticated production PM identity is implied by this developer-only route.
"""
import os
from functools import lru_cache
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentFailure, AgentSet
from app.api.supervisor_routes import _workers
from app.core.config import settings
from app.kb.loader import baselines, rate_cards, superseded_by
from app.llm.factory import LlmConfigError
from app.orchestration.local_run_store import LocalRunStore
from app.orchestration.progress import CaseProgress
from app.orchestration.run_coordination import AnswerQuestion, FinalReviewDecision, ResumeRun
from app.orchestration.run_state import RunInputs
from schema.schemas import Agent1Input
from app.orchestration.run_supervisor import RunSupervisorRuntime

_progress = {}
_lock = RLock()


def require_local(request: Request):
    if os.environ.get("PROTOTYPE_LAB") != "1":
        raise HTTPException(404, "로컬 테스트 모드에서만 사용할 수 있습니다.")
    if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "로컬 접속만 허용합니다.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "다른 사이트의 실행 요청은 허용하지 않습니다.")


router = APIRouter(prefix="/prototype/runs/cases", tags=["Local common Run"], dependencies=[Depends(require_local)])


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartBody(Body):
    input: Agent1Input
    rate_card_id: str
    provider: Literal["gpt", "local"] = "gpt"


class RevisionBody(Body):
    state_revision: int = Field(ge=0)


class AnswerBody(RevisionBody):
    question_id: str
    text: str = Field(min_length=1, max_length=4000)
    operation_id: str = Field(min_length=1)


class ReviewBody(RevisionBody):
    expected_snapshot_hash: str
    decision: Literal["approve", "hold", "reject"]
    reason: str = ""
    acknowledge_unknown_schedule: bool = False
    operation_id: str = Field(min_length=1)


@lru_cache(maxsize=1)
def get_store():
    if settings.backend_coordination_enabled and settings.backend_base_url and settings.backend_internal_token:
        from app.orchestration.backend_run_store import BackendRunStore
        actor = os.environ.get("WEAVE_ACTOR_MEMBER_ID", "00000000-0000-0000-0000-0000000000b1")
        project_id = os.environ.get("WEAVE_PROJECT_ID", "00000000-0000-0000-0000-0000000000a1")
        return BackendRunStore(settings.backend_base_url, settings.backend_internal_token, actor,
                               settings.prototype_run_db, project_id=project_id)
    return LocalRunStore(settings.prototype_run_db)


@lru_cache(maxsize=2)
def provider_workers(provider):
    # Cache stateless HTTP/model clients, never Run execution state.
    return _workers(provider)


def _runtime(store, case_id):
    workers = provider_workers(store.provider(case_id))
    return RunSupervisorRuntime(store, AgentSet(workers["agent1"], workers["agent2"], workers["agent3"]),
                                workers["llm"], workers["draft_llm"])


def progress_for(store, case_id):
    with _lock:
        if case_id not in _progress:
            _progress[case_id] = CaseProgress(store.progress(case_id))
        return _progress[case_id]


def _view(store, case_id):
    run = store.get_coordinated_run(case_id)
    outputs = {}
    for stage, state in run.state.agents.items():
        outputs[stage] = store.get_result(case_id, state.current_result_id).result_payload if state.current_result_id and not state.stale else None
    payload = outputs["agent3"]
    history = store.history(case_id)
    review = next((e for e in reversed(history) if e["event"] == "final_review"), None)
    reviewed = ({"review_id": review["operation_id"], "decision": review["decision"]} if review else None)
    target = run.review_bundle
    from app.agents.agent3.workflow import _hash
    snapshot_hash = _hash(target.model_dump()) if target else None
    pending = next((q for q in run.coordination.questions if q.status == "open"), None)
    hold = run.coordination.hold_reason
    if run.state.status == "WAITING_PM_REVIEW" and review and review["decision"] == "hold":
        hold = "PM 최종 검토 보류"
    is_backend = getattr(store, "is_backend_connected", False)
    storage_type = "backend-postgresql" if is_backend else "local-sqlite"
    view = {"impact": payload, "documents": payload.get("documents", {}), "snapshot_hash": snapshot_hash,
            "can_final_review": bool(target), "storage": storage_type} if payload else None
    return {"case_id": case_id, "run_id": case_id, "state_revision": run.state.state_revision,
        "input": store.get_inputs(case_id).agent1_input.model_dump(mode="json"),
        "provider": store.provider(case_id), "run_status": run.state.status,
        "answers": [a.model_dump(mode="json") for a in run.coordination.answers],
        "a1": outputs["agent1"], "a2": outputs["agent2"], "agent3": view,
        "question": pending.model_dump(mode="json") if pending else None,
        "ready_for_review": bool(target), "review": reviewed if run.state.status == "COMPLETED" or hold else None,
        "hold_reason": hold, "history": history, "progress": progress_for(store, case_id).after(),
        "storage": storage_type, "backend_connected": is_backend,
        "llm_calls": run.coordination.llm_calls, "segment_llm_calls": run.coordination.segment_llm_calls}


def _perform(store, case_id, operation):
    progress = progress_for(store, case_id)
    try:
        with progress.capture():
            return operation()
    except AgentFailure as exc:
        run = store.get_coordinated_run(case_id)
        if run.coordination.segment_llm_calls >= 40 or run.coordination.llm_calls >= 80:
            raise HTTPException(409, "모델 호출 한도에 도달했습니다. 현재 결과를 확인해 주세요.") from None
        raise HTTPException(502, {"message": exc.error.message, "retryable": exc.error.retryable}) from None
    except LlmConfigError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        store.save_progress(case_id, progress.after())


@router.post("")
def create(body: StartBody, store=Depends(get_store)):
    context = body.input.context
    baseline = baselines().get(context.baseline_id or "")
    if (not baseline or baseline["project_id"] != context.project_id or baseline["as_of"] != context.baseline_as_of
            or superseded_by(context.baseline_id) or body.rate_card_id not in rate_cards()):
        raise HTTPException(422, "프로젝트·기준선·단가표를 확인해 주세요.")
    # Validate credentials/configuration before creating any durable state.
    provider_workers(body.provider)
    state = store.create_run(context.case_id, RunInputs(agent1_input=body.input, rate_card_id=body.rate_card_id), provider=body.provider)
    _perform(store, context.case_id, lambda: _runtime(store, context.case_id).step(context.case_id, expected_revision=state.state_revision))
    return _view(store, context.case_id)


@router.get("/{case_id}")
def get_case(case_id: str, store=Depends(get_store)):
    return _view(store, case_id)


@router.get("/{case_id}/progress")
def progress(case_id: str, after: int = 0, store=Depends(get_store)):
    store.get_state(case_id)
    return {"events": progress_for(store, case_id).after(after)}


@router.post("/{case_id}/step")
def step(case_id: str, body: RevisionBody, store=Depends(get_store)):
    result = _perform(store, case_id, lambda: _runtime(store, case_id).step(case_id, expected_revision=body.state_revision))
    event = {key: result[key] for key in ("action", "status", "reason") if key in result}
    return {"event": event, "state": _view(store, case_id)}


@router.post("/{case_id}/answer")
def answer(case_id: str, body: AnswerBody, store=Depends(get_store)):
    store.answer_question(AnswerQuestion(run_id=case_id, expected_state_revision=body.state_revision,
        operation_id=body.operation_id, question_id=body.question_id, answer=body.text, answered_by="prototype-pm",
        disposition="unknown" if body.text.strip() == "모름" else "provided"))
    return _view(store, case_id)


@router.post("/{case_id}/resume")
def resume(case_id: str, body: RevisionBody, store=Depends(get_store)):
    store.resume_run(ResumeRun(run_id=case_id, expected_state_revision=body.state_revision,
                              operation_id=uuid4().hex, resumed_by="prototype-pm"))
    return _view(store, case_id)


@router.post("/{case_id}/review")
def review(case_id: str, body: ReviewBody, store=Depends(get_store)):
    from app.agents.agent3.workflow import _hash
    from app.orchestration.run_state import StateRevisionConflict, InvalidStateTransition
    previous = next((e for e in store.history(case_id) if e.get("operation_id") == body.operation_id), None)
    if previous:
        if previous["event"] == "final_review" and previous["decision"] == body.decision and _hash(previous["bundle"]) == body.expected_snapshot_hash:
            return _view(store, case_id)
        raise InvalidStateTransition("같은 작업 ID에 다른 검토 요청을 사용할 수 없습니다.", run_id=case_id)
    run = store.get_coordinated_run(case_id)
    if run.state.state_revision != body.state_revision:
        raise StateRevisionConflict("검토 대상이 변경되었습니다.", run_id=case_id)
    if not run.review_bundle or _hash(run.review_bundle.model_dump()) != body.expected_snapshot_hash:
        raise InvalidStateTransition("최신 결과와 문서를 확인해 주세요.", run_id=case_id)
    payload = store.get_result(case_id, run.state.agents["agent3"].current_result_id).result_payload
    if (body.decision == "approve" and any(e.get("schedule", {}).get("business_days") is None for e in payload["estimates"])
            and not body.acknowledge_unknown_schedule):
        raise HTTPException(422, "일정 미산정 사실을 확인해 주세요.")
    _runtime(store, case_id).review(FinalReviewDecision(run_id=case_id, expected_state_revision=body.state_revision,
        operation_id=body.operation_id, reviewed_by="prototype-pm", decision=body.decision, bundle=run.review_bundle))
    return _view(store, case_id)
