"""백엔드 → AI 범위 분석 요청 (C-01 이후). 백엔드 ai_analysis가 저장된 뒤 백엔드가 호출한다.

요청을 확인하면 바로 202로 답하고, Agent2는 백그라운드에서 실행한다 (LLM 판단은 수십 초 이상 걸릴 수 있다).
끝나면 결과를 백엔드 /internal/v1/ai-analyses/{id}/complete, 기술 실패는 /fail로 보낸다.
판단 보류(unclear)·입력 대기(NEEDS_INPUT)는 실패가 아니라 정상 결과(complete)다.
실패 코드는 공통 스키마 ErrorCode 값을 쓴다.
SQS가 생기면 이 입구 대신 큐 수신부가 같은 run_scope_analysis를 부른다.

인증: 백엔드와 같은 내부 토큰(X-Internal-Token = BACKEND_INTERNAL_TOKEN). 토큰이 비어 있으면 모두 거부한다.
"""
import logging
import secrets
import time
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel
from schema.schemas import Agent2Input

from app.agents.base import AgentFailure
from app.core.config import settings
from app.embed import EmbedConfigError
from app.kb.backend_kb import BackendKbError
from app.kb.vector_store import IndexMismatch
from app.llm.factory import LlmConfigError
from app.orchestration.ids import new_result_id, now_iso
from app.services.analysis_result_client import AnalysisReportError, build_client
from app.tools.errors import BaselineSuperseded, ToolInputError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/v1", tags=["Internal (Backend → AI)"])

REPORT_RETRIES = 3
REPORT_BACKOFF_SEC = 2.0


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")
    ai_analysis_id: UUID
    analysis_type: Literal["SCOPE"]
    input: Agent2Input


class AnalysisAccepted(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    ai_analysis_id: UUID
    status: Literal["ACCEPTED"] = "ACCEPTED"


def _require_token(token: Optional[str]) -> None:
    expected = settings.backend_internal_token
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "invalid internal token")


@router.post("/analyses", status_code=202, response_model=AnalysisAccepted, response_model_by_alias=True)
def request_analysis(req: AnalysisRequest, background: BackgroundTasks,
                     x_internal_token: Optional[str] = Header(None)) -> AnalysisAccepted:
    _require_token(x_internal_token)
    if not req.input.context.baseline_id:
        raise HTTPException(422, "context.baseline_id가 필요합니다.")
    background.add_task(run_scope_analysis, req.ai_analysis_id, req.input)
    return AnalysisAccepted(ai_analysis_id=req.ai_analysis_id)


def run_scope_analysis(ai_analysis_id: UUID, inp: Agent2Input) -> None:
    """Agent2를 실행하고 결과·실패를 백엔드에 보낸다. 예외를 밖으로 던지지 않는다 (백그라운드 작업)."""
    from app.api.agent2_routes import get_agent2
    from app.kb import loader

    failure: Optional[tuple[str, bool]] = None
    output: Optional[dict] = None
    try:
        loader.refresh_remote(inp.context.baseline_id or "")    # 현재 기준선에 문서가 더해졌을 수 있다
        out = get_agent2().run(inp)
        if out.status == "ERROR" and out.error is not None:
            failure = (out.error.code, out.error.retryable)
        else:
            meta = out.meta.model_copy(update={
                "result_id": out.meta.result_id or new_result_id("A2", inp.context.case_id),
                "created_at": out.meta.created_at or now_iso()})
            output = out.model_copy(update={"meta": meta}).model_dump(mode="json")
    except AgentFailure as exc:                             # 모델·검색 장애
        failure = (exc.error.code, exc.error.retryable)
    except BaselineSuperseded:                              # 기준선이 바뀜 — 같은 입력으로는 다시 해도 안 된다
        failure = ("VERSION_CONFLICT", False)
    except BackendKbError:                                  # 기준선 문서를 못 읽음 — 일시 장애일 수 있다
        failure = ("RETRIEVAL_UNAVAILABLE", True)
    except (LlmConfigError, EmbedConfigError, IndexMismatch):
        failure = ("INTERNAL_ERROR", False)
    except ValidationError:                                 # 에이전트 출력이 공통 스키마를 못 지킴
        failure = ("OUTPUT_INVALID", True)
    except (ToolInputError, ValueError, KeyError):          # 없는 기준선 등 입력 문제
        failure = ("INVALID_INPUT", False)
    except Exception:                                       # 예상 못 한 오류도 백엔드에 알려 QUEUED로 남지 않게 한다
        log.exception("범위 분석 실패 ai_analysis_id=%s", ai_analysis_id)
        failure = ("INTERNAL_ERROR", True)
    if failure:
        log.warning("범위 분석 실패 ai_analysis_id=%s code=%s retryable=%s", ai_analysis_id, *failure)
    _report(ai_analysis_id, output, failure)


def _report(ai_analysis_id: UUID, output: Optional[dict], failure: Optional[tuple[str, bool]]) -> None:
    try:
        client = build_client(settings)
    except ValueError:
        log.error("BACKEND_BASE_URL 없음 — 분석 결과를 보내지 못함 ai_analysis_id=%s", ai_analysis_id)
        return
    try:
        for attempt in range(1, REPORT_RETRIES + 1):
            try:
                if failure:
                    client.fail(ai_analysis_id, *failure)
                else:
                    client.complete(ai_analysis_id, output)
                return
            except AnalysisReportError as e:
                if not e.retryable or attempt == REPORT_RETRIES:
                    log.error("분석 결과 전달 실패 ai_analysis_id=%s status=%s: %s", ai_analysis_id, e.status_code, e)
                    return
                time.sleep(REPORT_BACKOFF_SEC * attempt)
    finally:
        client.close()
