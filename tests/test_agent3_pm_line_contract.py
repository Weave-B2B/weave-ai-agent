"""PM 시간 편집은 금액 공식 없이 Backend에 전달하고, 비청구 의도를 보존한다."""
import pytest
from pydantic import ValidationError
from schema.schemas import Estimate, Line, Schedule
from app.tools import calc_cost as module


def test_two_hour_pm_line_round_trip_preserves_billing_intent():
    line = Line(task="내부 검토", item_ids=["item"], role="PM", md=0.25, billable=False)
    estimate = Estimate(item_ids=["item"], status="draft", lines=[line],
                        schedule=Schedule(), rate_card_id="card")
    saved = Estimate.model_validate_json(estimate.model_dump_json())
    assert saved.lines[0].md == 0.25
    assert saved.lines[0].billable is False


def test_non_quarter_effort_is_rejected():
    with pytest.raises(ValidationError):
        Estimate(item_ids=["item"], status="draft", lines=[Line(task="검토", item_ids=["item"], role="PM", md=0.3)],
                 schedule=Schedule(), rate_card_id="card")


def test_thin_labor_adapter_forwards_nonbillable_flag(monkeypatch):
    seen = []
    class Backend:
        def labor_totals(self, payload):
            seen.append(payload)
            return {"backend_result": True}
    monkeypatch.setattr(module, "calculation_client", lambda _: Backend())
    assert module.calc_cost([{"role": "PM", "md": 0.25, "billable": False}], "card", project_id="project") == {"backend_result": True}
    assert seen[0]["lines"][0]["billable"] is False
    assert seen[0]["lines"][0]["md"] == 0.25
