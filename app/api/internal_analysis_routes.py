"""백엔드 → AI 분석 요청 (항목 분해·범위 분석·영향 산정). 백엔드 ai_analysis가 저장된 뒤 백엔드가 호출한다.

요청을 확인하면 바로 202로 답하고, 에이전트는 백그라운드에서 실행한다 (LLM 판단은 수십 초 이상 걸릴 수 있다).
DECOMPOSITION = Agent1 항목 분해, SCOPE = Agent2 범위 분석, IMPACT = Agent3 영향 산정.
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
from schema.schemas import Agent1Input, Agent2Input, Agent3Input

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
    analysis_type: Literal["DECOMPOSITION", "SCOPE", "IMPACT"]
    input: dict


INPUT_MODELS = {"DECOMPOSITION": Agent1Input, "SCOPE": Agent2Input, "IMPACT": Agent3Input}


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
    try:
        inp = INPUT_MODELS[req.analysis_type].model_validate(req.input)
    except ValidationError as exc:
        raise HTTPException(422, f"input이 {req.analysis_type} 입력 스키마와 맞지 않습니다: {exc.errors()[:3]}") from None
    # 항목 분해는 요청 원문만 보므로 기준선이 없어도 된다. 범위 분석·영향 산정은 기준선 문서로 판단한다
    if req.analysis_type != "DECOMPOSITION" and not inp.context.baseline_id:
        raise HTTPException(422, "context.baseline_id가 필요합니다.")
    task = {"DECOMPOSITION": run_decomposition, "SCOPE": run_scope_analysis,
            "IMPACT": run_impact_analysis}[req.analysis_type]
    background.add_task(task, req.ai_analysis_id, inp)
    return AnalysisAccepted(ai_analysis_id=req.ai_analysis_id)


def run_decomposition(ai_analysis_id: UUID, inp: Agent1Input) -> None:
    """Agent1(항목 분해)을 실행하고 결과·실패를 백엔드에 보낸다. 판단 로직은 /agent1/items와 같은 에이전트를 쓴다."""
    from app.api.agent1_routes import get_agent1

    failure: Optional[tuple[str, bool]] = None
    output: Optional[dict] = None
    try:
        out = get_agent1().run(inp)
        if out.status == "ERROR" and out.error is not None:
            failure = (out.error.code, out.error.retryable)
        else:
            meta = out.meta.model_copy(update={                 # /agent1/items와 같이 실행마다 새 결과 ID를 붙인다
                "result_id": new_result_id("A1", inp.context.case_id), "created_at": now_iso()})
            output = out.model_copy(update={"meta": meta}).model_dump(mode="json")
    except AgentFailure as exc:                             # 모델 장애
        failure = (exc.error.code, exc.error.retryable)
    except LlmConfigError:
        failure = ("INTERNAL_ERROR", False)
    except ValidationError:                                 # 에이전트 출력이 공통 스키마를 못 지킴
        failure = ("OUTPUT_INVALID", True)
    except (ValueError, KeyError):                          # 빈 원문, fixture에 없는 case 등 입력 문제
        failure = ("INVALID_INPUT", False)
    except Exception:
        log.exception("항목 분해 실패 ai_analysis_id=%s", ai_analysis_id)
        failure = ("INTERNAL_ERROR", True)
    if failure:
        log.warning("항목 분해 실패 ai_analysis_id=%s code=%s retryable=%s", ai_analysis_id, *failure)
    _report(ai_analysis_id, output, failure)


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


def run_impact_analysis(ai_analysis_id: UUID, inp: Agent3Input) -> None:
    """Agent3(영향 산정)를 실행하고 결과·실패를 백엔드에 보낸다. 범위 분석과 같은 결과 입구를 쓴다."""
    from app.agents.agent3.agent import LlmAgent3
    from app.kb import loader
    from app.llm.client import build_llm

    failure: Optional[tuple[str, bool]] = None
    output: Optional[dict] = None
    try:
        loader.refresh_remote(inp.context.baseline_id or "")    # 현재 기준선에 문서가 더해졌을 수 있다
        out = LlmAgent3(build_llm(settings)).run(inp)
        if out.status == "ERROR" and out.error is not None:
            failure = (out.error.code, out.error.retryable)
        else:
            meta = out.meta.model_copy(update={
                "result_id": out.meta.result_id or new_result_id("A3", inp.context.case_id),
                "created_at": out.meta.created_at or now_iso()})
            output = out.model_copy(update={"meta": meta}).model_dump(mode="json")
    except AgentFailure as exc:
        failure = (exc.error.code, exc.error.retryable)
    except BaselineSuperseded:
        failure = ("VERSION_CONFLICT", False)
    except BackendKbError:
        failure = ("RETRIEVAL_UNAVAILABLE", True)
    except (LlmConfigError, EmbedConfigError):
        failure = ("INTERNAL_ERROR", False)
    except ValidationError:
        failure = ("OUTPUT_INVALID", True)
    except (ToolInputError, ValueError, KeyError):                  # 없는 단가표·직군 등 입력 문제
        log.exception("영향 산정 입력 오류 ai_analysis_id=%s", ai_analysis_id)
        failure = ("INVALID_INPUT", False)
    except Exception:
        log.exception("영향 산정 실패 ai_analysis_id=%s", ai_analysis_id)
        failure = ("INTERNAL_ERROR", True)
    if failure:
        log.warning("영향 산정 실패 ai_analysis_id=%s code=%s retryable=%s", ai_analysis_id, *failure)
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
                if not failure and not e.retryable:      # 백엔드가 결과를 거부함 (검증 실패 등) — QUEUED로 남지 않게 실패로 알린다
                    log.error("분석 결과가 거부됨 ai_analysis_id=%s status=%s: %s", ai_analysis_id, e.status_code, e)
                    failure = ("OUTPUT_INVALID", False)
                    continue
                if not e.retryable or attempt == REPORT_RETRIES:
                    log.error("분석 결과 전달 실패 ai_analysis_id=%s status=%s: %s", ai_analysis_id, e.status_code, e)
                    return
                time.sleep(REPORT_BACKOFF_SEC * attempt)
    finally:
        client.close()
