from app.dto.analysis import AnalysisResultDTO


class PrototypeResultMapper:
    """Convert service DTOs into the response shape consumed by the existing prototype."""

    @staticmethod
    def to_view(result: AnalysisResultDTO) -> dict:
        state = result.workflow.model_dump(mode="json")
        a1 = (
            result.structured_request.structured.model_dump(mode="json")
            if result.structured_request
            else None
        )
        a2 = (
            result.scope_analysis.scope.model_dump(mode="json")
            if result.scope_analysis
            else None
        )
        a3 = (
            result.cost_estimation.cost.model_dump(mode="json")
            if result.cost_estimation
            else None
        )
        items = a1.get("items") if a1 else None
        questions = state.get("open_question_ids") or []
        return {
            "state": state,
            "a1": a1,
            "items": items,
            "a2": a2,
            "pm_review": None,
            "a3": a3,
            "questions": questions,
            "answers": [],
        }
