"""Agent1 단독 실행 엔드포인트.

공통 오케스트레이션(/cases)과 별도로 요청 원문을 항목화한다.
fixture 모드는 데이터셋 정답을 반환하고, llm 모드는 실제 Agent1을 실행한다.
결과 ID와 생성 시각은 호출 API 계층에서 발급한다.
"""
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.agents.base import AgentFailure
from app.core.config import settings
from app.llm.client import build_llm
from app.llm.factory import LlmConfigError
from app.orchestration.ids import new_result_id, now_iso
from schema.schemas import Agent1Input, Agent1Output

router = APIRouter(prefix="/agent1", tags=["Agent1"])


@lru_cache
def get_agent1():
    if "llm" in (settings.agent_mode, settings.agent1_mode):
        from app.agents.agent1.agent import LlmAgent1
        return LlmAgent1(build_llm(settings))
    from app.agents.fixture import FixtureAgent1, load_chain
    return FixtureAgent1(load_chain())


def _run(inp: Agent1Input) -> Agent1Output:
    try:
        out = get_agent1().run(inp)
    except AgentFailure as exc:
        raise HTTPException(502, exc.error.model_dump(mode="json")) from None
    except ValidationError:
        raise HTTPException(502, {
            "code": "OUTPUT_INVALID",
            "message": "Agent1 output failed schema validation",
            "stage": "agent1",
            "retryable": True,
        }) from None
    except LlmConfigError as exc:
        raise HTTPException(503, f"Agent1 설정 오류: {exc}") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except KeyError:
        raise HTTPException(404, f"fixture에 없는 case: {inp.context.case_id}") from None

    meta = out.meta.model_copy(update={
        "result_id": new_result_id("A1", inp.context.case_id),
        "created_at": now_iso(),
    })
    return out.model_copy(update={"meta": meta})


@router.post("/items", response_model=Agent1Output)
def itemize(inp: Agent1Input) -> Agent1Output:
    """요청 원문을 구조화 항목과 확인 질문으로 분해한다."""
    return _run(inp)
