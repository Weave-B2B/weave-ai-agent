from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import get_engine
from app.services.analysis_service import AnalysisNotFound, AnalysisService
from app.orchestration.engine import InvalidInput, InvalidTransition, VersionConflict
from schema.schemas import Agent1Input


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Agent1Input
    rate_card_id: str = Field(examples=["RC-SYNTH-2026H2-v2"])


router = APIRouter(prefix="/api/v1/analyses", tags=["Analysis Service"])


@lru_cache
def get_analysis_service() -> AnalysisService:
    return AnalysisService(get_engine())


def _guard(call):
    try:
        return call()
    except AnalysisNotFound as exc:
        raise HTTPException(404, "Analysis not found") from exc
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except InvalidInput as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("")
def create_analysis(req: AnalysisRequest):
    return _guard(lambda: get_analysis_service().start(req.input, req.rate_card_id).model_dump(mode="json"))


@router.get("/{analysis_id}")
def get_analysis(analysis_id: str):
    return _guard(lambda: get_analysis_service().get(analysis_id).model_dump(mode="json"))


@router.get("/{analysis_id}/status")
def get_analysis_status(analysis_id: str):
    return _guard(lambda: get_analysis_service().status(analysis_id))
