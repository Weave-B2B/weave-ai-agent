"""Synthetic cases exercise the actual Agent 3 workflow without calling Agent 1/2."""
import json
from pathlib import Path

import pytest

from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import Agent3Workflow, ReviewImpact, GenerateDraft
from app.llm.fake import FakeLlm
from schema.schemas import Agent3Input

SAMPLES = json.loads(Path("prototype/data/agent3-samples.json").read_text())
DEFAULT = json.loads(Path("examples/agent3/fake_responses.json").read_text())[0]


@pytest.mark.parametrize("case_id", sorted(SAMPLES["inputs"]))
def test_sample_case_runs_through_agent3(case_id):
    inp = Agent3Input.model_validate(SAMPLES["inputs"][case_id])
    response = SAMPLES["fake_responses"].get(case_id, DEFAULT)
    workflow = Agent3Workflow(LlmAgent3(FakeLlm([response, {"narrative": "안녕하세요. 변경 요청의 검토 내용을 전달드립니다.\n\n[[APPROVED_FACTS]]\n\n내용을 검토하신 뒤 함께 협의 부탁드립니다. 감사합니다."}])))
    result = workflow.prepare(inp)
    assert result["impact"]["context"]["case_id"] == case_id
    selected = [r for r in inp.pm_review.items if r.estimate]
    if not selected:
        assert result["phase"] == "NOT_REQUIRED"
    elif case_id in {"CR-B-003", "CR-C-007"}:
        assert result["phase"] == "WAITING_INPUT"
        assert all(e["totals"] is None for e in result["impact"]["estimates"])
    else:
        assert result["phase"] == "WAITING_IMPACT_APPROVAL"
        assert all(e["totals"] is not None for e in result["impact"]["estimates"])
        review = ReviewImpact(state_revision=result["state_revision"],
            target_result_id=result["impact"]["meta"]["result_id"], snapshot_hash=result["snapshot_hash"],
            request_id="test", reviewer_ref="test", approved=True, acknowledge_unknown_schedule=True)
        approved = workflow.review(result["session_id"], review)
        approved = workflow.generate_draft(result["session_id"], GenerateDraft(state_revision=approved["state_revision"], review_id=approved["approval"]["review_id"]))
        assert approved["phase"] == "DRAFT_READY"
        assert case_id in approved["draft"]["summary"]


def test_other_request_cannot_reuse_case_rules():
    inp = Agent3Input.model_validate(SAMPLES["inputs"]["CR-A-003"])
    inp.items[0].text += " 조건 변경"
    out = LlmAgent3(FakeLlm([SAMPLES["fake_responses"]["CR-A-003"]])).run(inp)
    assert out.status == "NEEDS_INPUT"
    assert out.estimates[0].totals is None


def test_common_sample_rate_card_matches_shared_test_baseline():
    from app.kb.loader import rate_cards
    common = 'RC-SYNTH-2026H2-v2'
    assert {entry['rate_card_id'] for entry in SAMPLES['inputs'].values()} == {common}
    assert rate_cards()[common]['rates'] == {
        'PM': {'cost': 520000, 'price': 750000},
        'UX': {'cost': 450000, 'price': 650000},
        'FE': {'cost': 450000, 'price': 650000},
        'BE': {'cost': 500000, 'price': 700000},
        'QA': {'cost': 350000, 'price': 500000},
    }
    assert 'QA' not in rate_cards()['RC-SYNTH-2026H2-v2-noQA']['rates']
