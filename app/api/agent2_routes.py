"""Agent2 단독 실행 엔드포인트 (화면·BE 연동 확인용). 공통 오케스트레이션(/cases)과 별개다.

AGENT2_MODE=llm(또는 AGENT_MODE=llm)이면 실제 에이전트, 아니면 데이터셋 정답(fixture)을 돌려준다.
결과 ID·생성 시각은 호출부인 이 API가 발급한다 (공통기준: LLM은 실행 ID를 만들지 않는다).
결과는 저장하지 않는다 — 저장은 BE 담당이며 DB 연결 전이다.
"""
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from schema.schemas import Agent2Input, Agent2Output

from app.agents.base import AgentFailure
from app.core.config import settings
from app.embed import EmbedConfigError
from app.kb.vector_store import IndexMismatch
from app.llm.factory import LlmConfigError
from app.orchestration.ids import new_result_id, now_iso
from app.tools.errors import ToolInputError

router = APIRouter(prefix="/agent2", tags=["Agent2"])


@lru_cache
def get_agent2():
    if "llm" in (settings.agent_mode, settings.agent2_mode):
        from app.agents.registry import build_agent2
        from app.llm.client import build_llm
        return build_agent2(build_llm(settings))
    from app.agents.fixture import FixtureAgent2, load_chain
    return FixtureAgent2(load_chain())


def _run(inp: Agent2Input) -> Agent2Output:
    try:
        out = get_agent2().run(inp)
    except AgentFailure as exc:                          # 모델·검색 장애: 오류 코드와 재시도 가능 여부를 그대로
        raise HTTPException(502, exc.error.model_dump(mode="json")) from None
    except (LlmConfigError, EmbedConfigError, IndexMismatch) as exc:
        raise HTTPException(503, f"Agent2 설정 오류: {exc}") from None
    except ValidationError:                              # 에이전트 출력이 공통 스키마를 못 지킴 — 입력 탓이 아니다
        raise HTTPException(502, {"code": "OUTPUT_INVALID", "message": "Agent2 output failed schema validation",
                                  "stage": "agent2", "retryable": True}) from None
    except (ToolInputError, ValueError) as exc:          # 없는 기준선 등 입력 문제
        raise HTTPException(422, str(exc)) from None
    except KeyError:                                     # fixture에 없는 case
        raise HTTPException(404, f"fixture에 없는 case: {inp.context.case_id}") from None
    meta = out.meta.model_copy(update={"result_id": out.meta.result_id or new_result_id("A2", inp.context.case_id),
                                       "created_at": out.meta.created_at or now_iso()})
    return out.model_copy(update={"meta": meta})


@router.post("/assessments", response_model=Agent2Output)
def assess(inp: Agent2Input) -> Agent2Output:
    """기능 항목별 범위 판단·근거·확인 질문. 판단 보류(unclear)·입력 대기(NEEDS_INPUT)는 오류가 아니라 정상 응답이다."""
    return _run(inp)
