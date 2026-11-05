import threading
import uuid

from app.dto.analysis import (
    AnalysisResultDTO,
    ContractRequestDTO,
    CostEstimationDTO,
    ScheduleReferenceDTO,
    ScopeAnalysisDTO,
    StructuredRequestDTO,
)
from app.orchestration.engine import Engine, InvalidInput, InvalidTransition, VersionConflict
from app.orchestration.state_store import CaseRecord
from app.tools.errors import ToolInputError
from app.tools.get_schedule import get_schedule
from schema.schemas import Agent1Input


class AnalysisNotFound(Exception):
    pass


class AnalysisService:
    """Service facade that keeps orchestration independent from FastAPI and prototype responses."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self._analysis_to_case: dict[str, str] = {}
        self._case_to_analysis: dict[str, str] = {}
        self._lock = threading.RLock()

    def start(self, inp: Agent1Input, rate_card_id: str) -> AnalysisResultDTO:
        ctx = inp.context
        analysis_id = ctx.analysis_id or f"analysis-{uuid.uuid4().hex}"
        inp = inp.model_copy(update={"context": ctx.model_copy(update={"analysis_id": analysis_id})})

        with self._lock:
            if ctx.case_id in self._case_to_analysis and self._case_to_analysis[ctx.case_id] != analysis_id:
                old = self.engine.store.get(ctx.case_id)
                if old is None:
                    self._case_to_analysis.pop(ctx.case_id, None)

        rec = self.engine.start(inp, rate_card_id)
        with self._lock:
            self._analysis_to_case[analysis_id] = ctx.case_id
            self._case_to_analysis[ctx.case_id] = analysis_id
        return self._to_result(analysis_id, rec)

    def get(self, analysis_id: str) -> AnalysisResultDTO:
        case_id = self._resolve_case_id(analysis_id)
        rec = self.engine.store.get(case_id)
        if rec is None:
            raise AnalysisNotFound(analysis_id)
        return self._to_result(analysis_id, rec)

    def status(self, analysis_id: str) -> dict:
        rec = self.engine.store.get(self._resolve_case_id(analysis_id))
        if rec is None:
            raise AnalysisNotFound(analysis_id)
        state = rec.state
        return {
            "analysis_id": analysis_id,
            "case_id": rec.inp.context.case_id,
            "status": state.status,
            "stage": state.stage,
            "state_revision": state.state_revision,
            "open_question_ids": state.open_question_ids,
            "rerun_from": state.rerun_from,
            "error": state.error.model_dump(mode="json") if state.error else None,
        }

    def _resolve_case_id(self, analysis_id: str) -> str:
        with self._lock:
            case_id = self._analysis_to_case.get(analysis_id)
        if case_id is None:
            raise AnalysisNotFound(analysis_id)
        return case_id

    def _to_result(self, analysis_id: str, rec: CaseRecord) -> AnalysisResultDTO:
        request = ContractRequestDTO(input=rec.inp)
        structured = (
            StructuredRequestDTO(request=request, structured=rec.a1)
            if rec.a1 is not None
            else None
        )
        scope = (
            ScopeAnalysisDTO(request=request, structured=rec.a1, scope=rec.a2)
            if rec.a1 is not None and rec.a2 is not None
            else None
        )
        cost = (
            CostEstimationDTO(
                request=request,
                structured=rec.a1,
                scope=rec.a2,
                cost=rec.a3,
            )
            if rec.a1 is not None and rec.a2 is not None and rec.a3 is not None
            else None
        )
        return AnalysisResultDTO(
            analysis_id=analysis_id,
            case_id=rec.inp.context.case_id,
            contract_request=request,
            structured_request=structured,
            scope_analysis=scope,
            cost_estimation=cost,
            schedule_reference=self._schedule_reference(rec),
            workflow=rec.state,
        )

    def _schedule_reference(self, rec: CaseRecord) -> ScheduleReferenceDTO | None:
        """기한·제약 항목이 있으면 판정 없이 계약 문서상의 현재 일정을 함께 돌려준다."""
        items = rec.items if rec.items is not None else (rec.a1.items if rec.a1 else [])
        item_ids = [i.item_id for i in items if i.item_type in ("deadline", "constraint")]
        if not item_ids:
            return None
        ctx = rec.inp.context
        try:
            found = get_schedule(ctx.project_id, ctx.baseline_as_of)
        except ToolInputError:
            return None
        return ScheduleReferenceDTO(
            item_ids=item_ids,
            as_of=found["as_of"],
            contract_end=found["contract_end"],
            contract_end_ref=found["contract_end_ref"],
            applied_change_orders=found["applied_change_orders"],
            milestones=found["milestones"],
            note="계약 문서에는 '오픈일'이 따로 없어 계약 종료(최종 인수)일을 기존 일정의 기준으로 표시합니다.",
        )
