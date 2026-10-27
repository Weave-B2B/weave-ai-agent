"""오케스트레이션 API (AI에이전트설계 v0.3 5장).

POST /cases/{id}/run                      흐름 시작 (① 실행 후 PM 항목 확인에서 멈춤)
GET  /cases/{id}/state                    현재 상태·단계별 결과
POST /cases/{id}/resume                   PM 항목 확인·PM 검토·리드 검토 입력으로 재개
POST /cases/{id}/questions/{qid}/answer   질문 답변 → 답변 분석 → 필요 시 재실행
POST /cases/{id}/retry                    기술 실패(FAILED) 단계 재시도
"""
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from app.agents.registry import build_agents
from app.api.models import AnswerRequest, ConfirmItems, LeadDecision, ResumeRequest, RetryRequest, RunRequest, SubmitPMReview
from app.orchestration.engine import Engine, InvalidInput, InvalidTransition, VersionConflict
from app.orchestration.state_store import InMemoryStore

router = APIRouter()


@lru_cache
def get_engine() -> Engine:
    return Engine(build_agents(), InMemoryStore())


def _guard(fn):
    try:
        return fn().view()
    except VersionConflict as e:
        raise HTTPException(409, str(e))
    except InvalidTransition as e:
        raise HTTPException(409, str(e))
    except InvalidInput as e:
        raise HTTPException(422, str(e))


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/cases/{case_id}/run")
def run_case(case_id: str, req: RunRequest):
    if req.input.context.case_id != case_id:
        raise HTTPException(422, "path case_id ≠ context.case_id")
    return _guard(lambda: get_engine().start(req.input, req.rate_card_id))


@router.get("/cases/{case_id}/state")
def get_state(case_id: str):
    rec = get_engine().store.get(case_id)
    if rec is None:
        raise HTTPException(404, "없는 case")
    return rec.view()


@router.post("/cases/{case_id}/resume")
def resume(case_id: str, req: ResumeRequest):
    e = get_engine()
    if isinstance(req, ConfirmItems):
        return _guard(lambda: e.confirm_items(case_id, req.items, req.state_revision))
    if isinstance(req, SubmitPMReview):
        return _guard(lambda: e.submit_pm_review(case_id, req.review, req.state_revision, req.new_evidence))
    assert isinstance(req, LeadDecision)
    return _guard(lambda: e.lead_decision(case_id, req.approved, req.state_revision, req.reason))


@router.post("/cases/{case_id}/questions/{question_id}/answer")
def answer(case_id: str, question_id: str, req: AnswerRequest):
    return _guard(lambda: get_engine().answer_question(case_id, question_id, req.answer, req.state_revision))


@router.post("/cases/{case_id}/retry")
def retry(case_id: str, req: RetryRequest):
    return _guard(lambda: get_engine().retry(case_id, req.state_revision))
