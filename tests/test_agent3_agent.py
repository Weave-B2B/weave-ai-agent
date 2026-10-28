"""Agent3 real assembly and tool use with offline model responses."""

import copy

import pytest

from conftest import EVAL, jl
from app.agents.agent3.agent import LlmAgent3
from app.agents.base import AgentFailure
from app.llm.fake import FakeLlm
from schema.schemas import Agent3Input


@pytest.fixture
def a3_input():
    rows = jl(EVAL / "inputs" / "agent3.jsonl") if (EVAL / "inputs" / "agent3.jsonl").exists() else jl(EVAL / "agent3" / "inputs.jsonl")
    return Agent3Input.model_validate(next(r for r in rows if r["context"]["case_id"] == "CR-A-001"))


@pytest.fixture
def a3_payload():
    return {"estimates": [{"item_numbers": [1], "status": "draft", "lines": [
        {"task": "리뷰 API", "item_numbers": [1], "role": "BE", "md": 2.5, "assumption": "기존 저장소 재사용"},
        {"task": "리뷰 UI, 선택적 사진 최대 3장", "item_numbers": [1], "role": "FE", "md": 2.0, "assumption": "사진 포함 가정"},
        {"task": "테스트", "item_numbers": [1], "role": "QA", "md": 0.5, "assumption": ""}
    ]}], "questions": []}


def test_agent3_uses_agent1_client_pattern_but_only_returns_impact(a3_input, a3_payload):
    model = FakeLlm([a3_payload])
    output = LlmAgent3(model).run(a3_input)
    assert output.status == "IMPACT_DRAFT_READY" and output.draft is None
    assert output.estimates[0].totals.md == 5.0
    assert output.estimates[0].totals.price == 3300000
    assert output.estimates[0].totals.cost == 2300000
    assert output.estimates[0].totals_source == "calc_cost"
    assert output.estimates[0].schedule.business_days is None
    assert output.meta.model == "fake:fake" and output.meta.agent_version.startswith("agent3-")
    assert model.calls[0]["schema"] == "Agent3ImpactDraft"
    assert "사진 최대 3장" in model.calls[0]["user"]
    assert "RC-SYNTH" not in model.calls[0]["user"]
    assert "gold_origin" not in model.calls[0]["user"]
    assert output.review_required and not output.billing_authorized
    assert output.meta.result_id is None  # Caller owns execution IDs.


def test_pending_review_never_calls_model(a3_input):
    a3_input.pm_review.status = "PENDING"
    model = FakeLlm([])
    out = LlmAgent3(model).run(a3_input)
    assert out.status == "WAITING_REVIEW" and not model.calls


def test_no_selected_items_never_calls_model(a3_input):
    for item in a3_input.pm_review.items:
        item.estimate = False
    model = FakeLlm([])
    out = LlmAgent3(model).run(a3_input)
    assert out.status == "NOT_REQUIRED" and not model.calls


def test_stale_upstream_is_rejected_before_llm(a3_input, a3_payload):
    a3_input.pm_review.target_result_id = "old-result"
    model = FakeLlm([a3_payload])
    with pytest.raises(AgentFailure) as error:
        LlmAgent3(model).run(a3_input)
    assert error.value.error.code == "VERSION_CONFLICT" and not model.calls


@pytest.mark.parametrize("mutation", ["money", "invalid_md"])
def test_untrusted_model_cannot_supply_money_or_invalid_md(a3_input, a3_payload, mutation):
    if mutation == "money":
        a3_payload["estimates"][0]["totals"] = {"price": 1}
    else:
        a3_payload["estimates"][0]["lines"][0]["md"] = 0.25
    model = FakeLlm([a3_payload, copy.deepcopy(a3_payload)])
    with pytest.raises(AgentFailure) as error:
        LlmAgent3(model).run(a3_input)
    assert error.value.error.code == "OUTPUT_INVALID"


@pytest.mark.parametrize("mode", ["missing", "duplicate", "foreign_line"])
def test_proposal_must_cover_exactly_the_pm_selected_targets(a3_input, a3_payload, mode):
    if mode == "missing":
        a3_payload["estimates"][0]["item_numbers"] = [2]
    elif mode == "duplicate":
        a3_payload["estimates"].append(copy.deepcopy(a3_payload["estimates"][0]))
    else:
        a3_payload["estimates"][0]["lines"][0]["item_numbers"] = [2]
    with pytest.raises(AgentFailure) as error:
        LlmAgent3(FakeLlm([a3_payload])).run(a3_input)
    assert error.value.error.code == "OUTPUT_INVALID"


def test_missing_rate_yields_question_not_partial_total(a3_input, a3_payload):
    a3_input.rate_card_id = "RC-SYNTH-2026H2-noQA"
    out = LlmAgent3(FakeLlm([a3_payload])).run(a3_input)
    assert out.status == "NEEDS_INPUT" and out.draft is None
    assert all(e.totals is None for e in out.estimates)
    assert any(q.blocking and q.field == "rate:QA" for q in out.questions)


def test_not_estimable_has_no_invented_effort_and_has_followup(a3_input):
    payload = {"estimates": [{"item_numbers": [1], "status": "not_estimable", "reason": "요구 상세 부족", "lines": []}]}
    out = LlmAgent3(FakeLlm([payload])).run(a3_input)
    assert out.estimates[0].status == "not_estimable"
    assert not out.estimates[0].lines and out.estimates[0].totals is None
    assert out.questions and out.estimates[0].schedule.business_days is None


def test_blocking_question_preserves_waiting_input(a3_input, a3_payload):
    a3_payload["questions"] = [{"item_numbers": [1], "text": "필수 조건?", "reason": "필수 정보 없음", "blocking": True}]
    out = LlmAgent3(FakeLlm([a3_payload])).run(a3_input)
    assert out.status == "NEEDS_INPUT" and out.draft is None
