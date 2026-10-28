"""Agent3 MVP endpoints for trusted backend integration, separate from shared engine."""

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import Agent3Workflow, EditImpact, ReviewImpact, WorkflowConflict
from app.agents.base import AgentFailure
from app.core.config import settings
from app.llm.client import build_llm
from app.tools.errors import ToolInputError
from schema.schemas import Agent3Input

router = APIRouter(prefix="/agent3", tags=["Agent3 MVP"])


@lru_cache
def get_workflow() -> Agent3Workflow:
    return Agent3Workflow(LlmAgent3(build_llm(settings)))


def _guard(call):
    try:
        return call()
    except KeyError:
        raise HTTPException(404, "Agent3 session not found") from None
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except AgentFailure as exc:
        raise HTTPException(502, exc.error.model_dump(mode="json")) from None
    except (ToolInputError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "configured Agent3 data is unavailable") from None


@router.post("/impacts")
def estimate(inp: Agent3Input):
    return _guard(lambda: get_workflow().prepare(inp))


@router.get("/impacts/{session_id}")
def get_impact(session_id: str):
    return _guard(lambda: get_workflow().get(session_id))


@router.post("/impacts/{session_id}/edit")
def edit_impact(session_id: str, req: EditImpact):
    return _guard(lambda: get_workflow().edit(session_id, req))


@router.post("/impacts/{session_id}/review")
def review_impact(session_id: str, req: ReviewImpact):
    return _guard(lambda: get_workflow().review(session_id, req))
