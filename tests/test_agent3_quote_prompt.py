"""Scope exclusion requests are pre-approval quotes, not contract approval actions."""
import json

from app.agents.agent3.contracts import ProvisionalAgent3Input
from app.agents.agent3.prompts import PLAN_SYSTEM, build_user_prompt
from conftest import EVAL, jl
from schema.schemas import Agent3Input


def _input():
    path = EVAL / "inputs" / "agent3.jsonl"
    if not path.exists():
        path = EVAL / "agent3" / "inputs.jsonl"
    return Agent3Input.model_validate(next(r for r in jl(path) if r["context"]["case_id"] == "CR-A-001"))


def test_selected_pm_reason_reaches_the_estimation_model():
    inp = _input()
    selected = next(row for row in inp.pm_review.items if row.estimate)
    selected.reason = "기존 범위 밖 기능의 승인 전 별도 검토 견적을 요청합니다."
    prompt = json.loads(build_user_prompt(inp))
    target = next(row for row in prompt["targets"] if row["scope_review"]["reason"] == selected.reason)
    assert target["scope_review"]["pm_label"] == selected.pm_label
    assert target["scope_review"]["estimate"] is True
    assert prompt["estimation_context"] == {
        "purpose": "pre_approval_quote", "billing_authorized": False, "selected_by": "pm_review"}
    assert "out_of_scope는 기존 계약에 포함되지 않아 추가 견적이 필요한 항목" in PLAN_SYSTEM
    assert "계약 변경·착수·청구 승인과 별개" in PLAN_SYSTEM


def test_unselected_pm_items_are_not_promoted_to_quote_targets():
    inp = _input()
    for row in inp.pm_review.items:
        row.estimate = False
    assert json.loads(build_user_prompt(inp))["targets"] == []


def test_supervisor_provisional_quote_does_not_invent_pm_review():
    original = _input()
    inp = ProvisionalAgent3Input.model_validate({
        "context": original.context.model_dump(), "items": [row.model_dump() for row in original.items],
        "constraints": [row.model_dump() for row in original.constraints], "answers": [],
        "received_at": original.received_at,
        "target_item_ids": [row.item_id for row in original.targets()],
        "a2_result": original.a2_result.model_dump(), "rate_card_id": original.rate_card_id})
    prompt = json.loads(build_user_prompt(inp))
    assert prompt["estimation_context"]["selected_by"] == "supervisor"
    assert prompt["estimation_context"]["billing_authorized"] is False
    assert prompt["targets"]
    assert all(row["scope_review"] is None for row in prompt["targets"])
