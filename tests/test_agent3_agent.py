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
        {"task": "리뷰 API", "item_numbers": [1], "role": "BE", "rule_id": "review_be", "md": 2.5, "assumption": "기존 저장소 재사용"},
        {"task": "리뷰 UI, 선택적 사진 최대 3장", "item_numbers": [1], "role": "FE", "rule_id": "review_fe", "md": 2.0, "assumption": "사진 포함 가정"},
        {"task": "테스트", "item_numbers": [1], "role": "QA", "rule_id": "review_qa", "md": 0.5, "assumption": ""}
    ]}], "questions": []}


def test_agent3_uses_agent1_client_pattern_but_only_returns_impact(a3_input, a3_payload):
    model = FakeLlm([a3_payload])
    output = LlmAgent3(model).run(a3_input)
    assert output.status == "IMPACT_DRAFT_READY" and output.draft is None
    assert output.estimates[0].totals.md == 8.0
    assert output.estimates[0].totals.price == 5150000
    assert output.estimates[0].totals.cost == 3590000
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


def test_missing_rule_blocks_calculation(a3_input, a3_payload):
    a3_payload["estimates"][0]["lines"][0]["rule_id"] = "unknown"
    out = LlmAgent3(FakeLlm([a3_payload])).run(a3_input)
    assert out.status == "NEEDS_INPUT"
    assert out.estimates[0].status == "not_estimable"
    assert out.estimates[0].totals is None
    assert any(q.field == "effort_rule" and q.blocking for q in out.questions)


def test_example_rules_do_not_apply_to_changed_request(a3_input, a3_payload):
    a3_input.items[0].text = "다른 기능 요청"
    out = LlmAgent3(FakeLlm([a3_payload])).run(a3_input)
    assert out.status == "NEEDS_INPUT"
    assert out.estimates[0].totals is None


@pytest.mark.parametrize('problem', ['unknown', 'wrong_role', 'duplicate'])
def test_model_rule_errors_retry_once_without_inventing_md(a3_input, a3_payload, problem):
    invalid = copy.deepcopy(a3_payload)
    if problem == 'unknown':
        invalid['estimates'][0]['lines'][0]['rule_id'] = 'not-a-real-rule'
    elif problem == 'wrong_role':
        invalid['estimates'][0]['lines'][0]['role'] = 'FE'
    else:
        invalid['estimates'][0]['lines'].append(copy.deepcopy(invalid['estimates'][0]['lines'][0]))
    model = FakeLlm([invalid, a3_payload])
    out = LlmAgent3(model).run(a3_input)
    assert len(model.calls) == 2
    assert out.status == 'IMPACT_DRAFT_READY'
    assert out.estimates[0].totals.md == 8
    assert out.meta.debug['effort_rule_retry'] is True
    assert '기준 ID' in model.calls[1]['user']


def test_rule_retry_does_not_guess_when_retry_remains_invalid(a3_input, a3_payload):
    invalid = copy.deepcopy(a3_payload)
    invalid['estimates'][0]['lines'][0]['rule_id'] = 'not-a-real-rule'
    model = FakeLlm([invalid, invalid])
    out = LlmAgent3(model).run(a3_input)
    assert len(model.calls) == 2 and out.status == 'NEEDS_INPUT'
    assert out.estimates[0].totals is None and not out.estimates[0].lines
    assert any(q.field == 'effort_rule' and q.blocking for q in out.questions)


def test_changed_target_never_retries_to_force_a_sample_rule(a3_input, a3_payload):
    a3_input.items[0].text = '완전히 다른 작업'
    model = FakeLlm([a3_payload])
    out = LlmAgent3(model).run(a3_input)
    assert len(model.calls) == 2
    assert out.status == 'NEEDS_INPUT' and out.estimates[0].totals is None


def test_changed_review_request_excludes_sample_rule_and_retries_with_general_rule(a3_input, a3_payload):
    import json
    a3_input.items[0].text = '상품 리뷰 기능 추가 — 구매자 별점·후기 텍스트, 사진 최대 3장(선택 사항)'
    revised = {'estimates': [{'item_numbers': [1], 'status': 'draft', 'lines': [
        {'item_numbers': [1], 'task': '리뷰 작성 화면', 'role': 'FE', 'rule_id': 'general_fe_medium', 'assumption': '기존 화면 변경'}]}], 'questions': []}
    model = FakeLlm([a3_payload, revised])
    out = LlmAgent3(model).run(a3_input)
    available = json.loads(model.calls[0]['user'])['test_effort_rules']['rules']
    assert 'review_fe' not in available and 'general_fe_medium' in available
    assert len(model.calls) == 2 and out.estimates[0].totals.md == 3.0
    assert out.meta.debug['effort_rule_retry'] is True


def test_missing_implementation_details_can_be_estimated_as_reviewable_assumptions(a3_input):
    a3_input.items[0].text = '상품 리뷰 기능: 별점·후기와 선택적 사진 3장'
    initial = {'estimates': [{'item_numbers': [1], 'status': 'not_estimable', 'reason': '사진 저장 방식 미정', 'lines': []}],
               'questions': [{'item_numbers': [1], 'text': '사진 저장 방식?', 'reason': '저장 방식 미정', 'blocking': True}]}
    revised = {'estimates': [{'item_numbers': [1], 'status': 'draft', 'lines': [
        {'item_numbers': [1], 'task': '리뷰 저장 및 사진 처리', 'role': 'BE', 'rule_id': 'general_be_medium',
         'assumption': '기존 사진 저장 기능 재사용, 외부 연동 제외; 사진 포함 가정'}]}],
        'questions': [{'item_numbers': [1], 'text': '기존 사진 저장 기능을 재사용할 수 있나요?', 'reason': '임시 가정 확인', 'blocking': False}]}
    model = FakeLlm([initial, revised])
    out = LlmAgent3(model).run(a3_input)
    assert len(model.calls) == 2 and out.meta.debug['assumption_retry'] is True
    assert out.status == 'IMPACT_DRAFT_READY' and out.estimates[0].totals.md == 3
    assert '기존 사진 저장 기능 재사용' in out.estimates[0].lines[0].assumption
    assert out.questions and not out.questions[0].blocking


def test_missing_scope_remains_blocked_when_assumptions_are_insufficient(a3_input):
    payload = {'estimates': [{'item_numbers': [1], 'status': 'not_estimable', 'reason': '요청 기능 및 규모 불명', 'lines': []}],
               'questions': [{'item_numbers': [1], 'text': '구현할 기능의 범위는?', 'reason': '규모 불명', 'blocking': True}]}
    out = LlmAgent3(FakeLlm([payload, payload])).run(a3_input)
    assert out.status == 'NEEDS_INPUT' and out.estimates[0].totals is None
    assert out.questions[0].blocking


def test_general_test_rule_estimates_multiple_pm_selected_items(a3_input):
    first = a3_input.items[0]
    first.text = '기존 화면에 검색 입력 추가'
    second = first.model_copy(deep=True)
    second.item_id = f'{a3_input.context.case_id}-new'
    second.text = '내부 API에서 검색 결과 조회'
    a3_input.items.append(second)
    assessment = a3_input.a2_result.assessments[0].model_copy(deep=True)
    assessment.item_id = second.item_id
    a3_input.a2_result.assessments.append(assessment)
    review = a3_input.pm_review.items[0].model_copy(deep=True)
    review.item_id = second.item_id
    a3_input.pm_review.items.append(review)
    payload = {'estimates': [
        {'item_numbers': [1], 'status': 'draft', 'lines': [
            {'item_numbers': [1], 'task': '검색 입력 UI', 'role': 'FE', 'rule_id': 'general_fe_small', 'assumption': '기존 화면 변경'}]},
        {'item_numbers': [2], 'status': 'draft', 'lines': [
            {'item_numbers': [2], 'task': '검색 조회', 'role': 'BE', 'rule_id': 'general_be_small', 'assumption': '기존 내부 API'}]},
    ], 'questions': []}
    model = FakeLlm([payload])
    out = LlmAgent3(model).run(a3_input)
    assert out.status == 'IMPACT_DRAFT_READY'
    assert [e.item_ids for e in out.estimates] == [[first.item_id], [second.item_id]]
    assert [e.totals.md for e in out.estimates] == [1.0, 1.5]
    assert all('임시 기준' in e.lines[0].assumption for e in out.estimates)
    assert 'general_fe_small' in model.calls[0]['user']
