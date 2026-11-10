"""Provisional tools, verified eligibility, partial reruns and final snapshot review."""
import copy

import pytest
from fastapi.testclient import TestClient

from test_agent3_agent import a3_input, a3_payload
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.contracts import ProvisionalAgent3Input
from app.agents.agent3.orchestration import (
    Agent3Orchestration, DraftDocuments, Recalculate, Reestimate, FinalReview, _combine,
)
from app.agents.agent3.workflow import FAKE_EMAIL, WorkflowConflict
from app.api import agent3_routes
from app.llm.fake import FakeLlm
from app.main import app
from schema.schemas import Schedule
from app.agents.agent3.contracts import ProvisionalEstimate as Estimate, ProvisionalLine as Line


@pytest.fixture
def provisional(a3_input):
    raw = a3_input.model_dump(exclude={"pm_review"})
    raw["target_item_ids"] = [i.item_id for i in a3_input.targets()]
    return ProvisionalAgent3Input.model_validate(raw)


@pytest.fixture
def tools(a3_payload):
    return Agent3Orchestration(LlmAgent3(FakeLlm([copy.deepcopy(a3_payload) for _ in range(10)])),
        draft_llm=FakeLlm([copy.deepcopy(FAKE_EMAIL) for _ in range(10)]))


def add_verified_assessment(inp, item_id):
    assessment = inp.a2_result.assessments[0].model_copy(deep=True)
    assessment.item_id = item_id
    inp.a2_result.assessments.append(assessment)


def documents(tools, view, **changes):
    return tools.draft_documents(view["session_id"], DraftDocuments(state_revision=view["state_revision"], **changes))


def review(view, **changes):
    fields = dict(state_revision=view["state_revision"], snapshot_hash=view["snapshot_hash"],
        request_id="final-1", reviewer_ref="trusted-backend-pm", decision="approve", acknowledge_unknown_schedule=True)
    fields.update(changes)
    return FinalReview(**fields)


def test_provisional_impact_and_customer_documents_before_any_pm_review(tools, provisional):
    original = provisional.model_dump()
    view = tools.estimate_impact(provisional)
    assert provisional.model_dump() == original
    assert view["impact"]["estimates"][0]["totals"]["md"] == 8
    assert view["impact"]["meta"]["upstream_result_ids"] == [provisional.a2_result.meta.result_id]
    assert not view["approval"] and view["provisional"]
    view = documents(tools, view)
    assert view["phase"] == "WAITING_FINAL_REVIEW"
    assert set(view["documents"]) == {"email", "agreement"}
    text = view["documents"]["email"]["body"]
    assert "5,150,000원" in text and "고객 협의 전" in text and "기존 계약 범위" not in text
    assert "원가" not in text and "마진" not in text and "RC-SYNTH" not in text and "synthetic-" not in text
    assert "FSP-A-001-v1.0" in text
    request = review(view)
    approved = tools.review(view["session_id"], request)
    assert approved["phase"] == "FINAL_APPROVED"
    assert approved["approval"]["snapshot_hash"] == view["snapshot_hash"]
    assert tools.review(view["session_id"], request) == approved
    assert not approved["impact"]["billing_authorized"]
    with pytest.raises(WorkflowConflict):
        documents(tools, approved)


def test_customer_email_is_sectioned_without_raw_contract_dump(tools, provisional):
    view = documents(tools, tools.estimate_impact(provisional))
    email = view["documents"]["email"]["body"]
    agreement = view["documents"]["agreement"]["body"]
    for heading in ("1. 요청 사항 및 범위 검토", "2. 예상 작업 및 공수 (잠정)",
                    "3. 예상 견적 및 일정", "4. 산정 전제 및 요청 조건",
                    "5. 고객 확인 및 협의 사항"):
        assert heading in email
    assert "검토 요지:" in email and "확인 근거:" in email
    assert "- 총 예상 공수: 8 MD" in email
    assert "- 고객 제안 금액: 5,150,000원 (VAT 별도)" in email
    assert "고객 협의 전 잠정 검토안" in email
    assert "원가" not in email and "마진" not in email and "RC-SYNTH" not in email
    # Full verified clause quotes remain in the separate agreement; email shows references.
    quote = next(e.quote for a in provisional.a2_result.assessments
                 for e in a.evidence if e.verified)
    assert quote in agreement
    assert quote not in email


def test_forged_evidence_is_reverified_and_deferred_without_llm(tools, provisional):
    a = provisional.a2_result.assessments[0]
    a.evidence[0].quote = "forged quotation"
    a.evidence[0].verified = True
    a.evidence = a.evidence[:1]
    result = tools.estimate_impact(provisional)
    assert not result["impact"]["estimates"]
    assert result["needs"][0]["kind"] == "more_evidence"
    assert not tools.agent.llm.calls
    result = documents(tools, result)
    assert result["phase"] == "WAITING_INPUT"
    with pytest.raises(ValueError):
        tools.review(result["session_id"], review(result))


def test_wrong_document_version_cannot_authorize_estimation(tools, provisional):
    for evidence in provisional.a2_result.assessments[0].evidence:
        evidence.doc_version = "wrong-version"
    view = tools.estimate_impact(provisional)
    assert not view["impact"]["estimates"]
    assert view["needs"][0]["kind"] == "more_evidence"
    assert not tools.agent.llm.calls


def test_in_scope_confirmation_can_generate_scope_reply_without_estimation(tools, provisional):
    provisional.items[0].intent = "confirmation"
    provisional.a2_result.assessments[0].label = "in_scope"
    result = tools.estimate_impact(provisional)
    assert result["needs"][0]["kind"] == "not_required"
    result = documents(tools, result, kind="scope_reply")
    assert "기존 범위 내" in result["documents"]["email"]["body"]
    assert "agreement" not in result["documents"]
    assert "0 MD" not in result["documents"]["email"]["body"]
    assert tools.review(result["session_id"], review(result))["phase"] == "FINAL_APPROVED"
    assert not tools.agent.llm.calls


def test_conditional_confirmation_and_explicit_quote_request(tools, provisional):
    provisional.items[0].intent = "confirmation"
    provisional.conditional_item_ids = provisional.target_item_ids[:]
    assert tools.estimate_impact(provisional)["impact"]["estimates"]
    provisional.items[0].intent = "estimate_request"
    assert tools.estimate_impact(provisional)["impact"]["estimates"]
    provisional.a2_result.assessments = []
    blocked = tools.estimate_impact(provisional)
    assert not blocked["impact"]["estimates"]
    assert blocked["needs"][0]["kind"] == "more_evidence"


def test_mixed_ready_and_unclear_items_preserve_partial_result(tools, provisional):
    second = provisional.items[0].model_copy(deep=True)
    second.item_id = provisional.context.case_id + "-99"
    second.text = "추가 기능"
    provisional.items.append(second)
    provisional.target_item_ids.append(second.item_id)
    assessment = provisional.a2_result.assessments[0].model_copy(deep=True)
    assessment.item_id, assessment.label, assessment.evidence = second.item_id, "unclear", []
    assessment.evidence_level = "none"
    assessment.missing_info = ["요구사항 근거 부족"]
    provisional.a2_result.assessments.append(assessment)
    result = tools.estimate_impact(provisional)
    assert len(result["impact"]["estimates"]) == 1
    assert result["needs"][0]["item_ids"] == [second.item_id]
    assert result["phase"] == "WAITING_INPUT"


def test_llm_more_evidence_need_keeps_topic_and_target(provisional, a3_payload):
    a3_payload["needs"] = [{"kind": "more_evidence", "item_numbers": [1], "topic": "사진 저장 제외 조항"}]
    tools = Agent3Orchestration(LlmAgent3(FakeLlm([a3_payload])))
    result = tools.estimate_impact(provisional)
    assert result["needs"][0]["kind"] == "more_evidence"
    assert result["needs"][0]["topic"] == "사진 저장 제외 조항"
    assert not result["can_final_review"]


def test_recalculation_invalidates_documents_and_review_without_model_call(tools, provisional):
    view = documents(tools, tools.estimate_impact(provisional))
    approved = tools.review(view["session_id"], review(view))
    calls = len(tools.agent.llm.calls)
    edited = tools.recalculate(approved["session_id"], Recalculate(state_revision=approved["state_revision"],
        edits=[{"work_id": "0:0", "md": 4.5}], reason="PM 공수 수정"))
    assert edited["impact"]["estimates"][0]["totals"]["price"] == 5850000
    assert edited["approval"] is None and edited["documents"] == {}
    assert edited["history"][-1]["approval"]
    assert len(tools.agent.llm.calls) == calls
    with pytest.raises(WorkflowConflict):
        tools.review(view["session_id"], review(view, request_id="stale"))


def test_partial_reestimation_preserves_unrelated_item(tools, provisional):
    # Scope-confirmation item has no estimate and must remain in the final reply.
    second = provisional.items[0].model_copy(deep=True)
    second.item_id, second.intent = provisional.context.case_id + "-99", "confirmation"
    provisional.items.append(second)
    view = tools.estimate_impact(provisional)
    replacement = provisional.model_copy(deep=True)
    replacement.items[0].text += " 작업 조건 검토"
    replacement.a2_result.meta.result_id += "-new"
    result = tools.reestimate(view["session_id"], Reestimate(state_revision=view["state_revision"],
        input=replacement, item_ids=[provisional.items[0].item_id]))
    assert result["impact"]["meta"]["upstream_result_ids"] == [replacement.a2_result.meta.result_id, view["impact"]["meta"]["result_id"]]
    assert result["impact"]["estimates"] and not result["documents"]
    replacement.items[1].text += " changed"
    with pytest.raises(ValueError, match="all items"):
        tools.reestimate(result["session_id"], Reestimate(state_revision=result["state_revision"],
            input=replacement, item_ids=[provisional.items[0].item_id]))


def test_shared_work_combines_but_same_role_or_rule_does_not():
    def estimate(item, shared=None):
        return Estimate(item_ids=[item], status="draft", rate_card_id="test",
            lines=[Line(task="공통 인증 작업", item_ids=[item], role="BE", md=2, shared_work_key=shared)], schedule=Schedule())
    shared = _combine([estimate("A", "auth"), estimate("B", "auth")])
    assert len(shared) == 1 and len(shared[0].lines) == 1
    assert shared[0].lines[0].item_ids == ["A", "B"]
    assert len(_combine([estimate("A"), estimate("B")])) == 2
    conflicting = estimate("B", "auth")
    conflicting.lines[0].md = 3
    with pytest.raises(ValueError, match="conflicting"):
        _combine([estimate("A", "auth"), conflicting])


def test_hold_can_resume_with_final_review_and_unknown_schedule_ack(tools, provisional):
    view = documents(tools, tools.estimate_impact(provisional))
    with pytest.raises(ValueError, match="schedule"):
        tools.review(view["session_id"], review(view, acknowledge_unknown_schedule=False))
    held = tools.review(view["session_id"], review(view, decision="hold", reason="확인 중"))
    assert held["phase"] == "ON_HOLD"
    approved = tools.review(view["session_id"], review(held, request_id="final-2"))
    assert approved["phase"] == "FINAL_APPROVED"


def test_routes_expose_provisional_contract_and_do_not_accept_fake_pm_review(tools, provisional, monkeypatch):
    monkeypatch.setattr(agent3_routes, "get_orchestration", lambda: tools)
    with TestClient(app) as client:
        response = client.post("/agent3/orchestration/impacts", json=provisional.model_dump(mode="json"))
        assert response.status_code == 200
        view = response.json()
        response = client.post(f"/agent3/orchestration/{view['session_id']}/documents", json={"state_revision": 1})
        assert response.status_code == 200
        assert response.json()["approval"] is None
        raw = provisional.model_dump(mode="json")
        raw["pm_review"] = {"status": "REVIEWED"}
        assert client.post("/agent3/orchestration/impacts", json=raw).status_code == 422


def test_new_session_rejects_old_mutations(tools, provisional):
    old = tools.estimate_impact(provisional)
    tools.estimate_impact(provisional)
    with pytest.raises(WorkflowConflict, match="newer"):
        documents(tools, old)


def test_omitted_change_cannot_silently_pass_final_review(tools, provisional):
    provisional.target_item_ids = []
    result = tools.estimate_impact(provisional)
    assert result["needs"][0]["kind"] == "client_input"
    assert not result["can_final_review"]


@pytest.mark.parametrize("provider", ["fake", "openai", "gemini"])
def test_local_tools_use_the_same_contract_for_every_provider(provisional, a3_payload, monkeypatch, provider):
    from fastapi import FastAPI
    from prototype import lab
    from app.llm.base import RawResponse
    class Client(FakeLlm):
        def _call(self, **kwargs):
            if kwargs["tool_name"] == "submit_negotiation_draft":
                return RawResponse(payload=copy.deepcopy(FAKE_EMAIL))
            return super()._call(**kwargs)
    client = Client([copy.deepcopy(a3_payload)] * 3)
    monkeypatch.setattr(lab, "provider_client", lambda body: client)
    test_app = FastAPI()
    test_app.include_router(lab.router)
    with TestClient(test_app) as http:
        raw = {"input": provisional.model_dump(mode="json"), "provider": provider}
        response = http.post("/prototype-lab/agent3-orchestration", json=raw)
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["approval"] is None and view["impact"]["estimates"][0]["totals"]
        response = http.post(f"/prototype-lab/agent3-orchestration/{view['session_id']}/documents",
            json={"state_revision": view["state_revision"]})
        assert response.status_code == 200, response.text
        assert response.json()["phase"] == "WAITING_FINAL_REVIEW"
        assert set(response.json()["documents"]) == {"email", "agreement"}
        response = http.post("/prototype-lab/agent3-orchestration", json=raw, headers={"Origin": "https://example.com"})
        assert response.status_code == 403


def test_partial_reestimate_expands_shared_group(provisional):
    first = provisional.items[0]
    second = first.model_copy(deep=True)
    second.item_id = first.case_id + "-2"
    second.intent = first.intent = "estimate_request"
    provisional.items.append(second)
    provisional.target_item_ids.append(second.item_id)
    add_verified_assessment(provisional, second.item_id)
    payload = {"estimates": [{"item_numbers": [1, 2], "status": "draft", "lines": [
        {"task": "인증 API", "item_numbers": [1, 2], "role": "BE", "rule_id": "general_be_small",
         "shared_work_key": "auth", "assumption": "기존 인증 재사용"}]}], "questions": []}
    # Select an actual generic BE rule rather than relying on its name.
    from app.agents.agent3.effort import load_effort_rules
    payload["estimates"][0]["lines"][0]["rule_id"] = next(k for k, v in load_effort_rules()["rules"].items() if v.get("kind") == "general_test" and v["role"] == "BE")
    tools = Agent3Orchestration(LlmAgent3(FakeLlm([copy.deepcopy(payload)] * 2)))
    view = tools.estimate_impact(provisional)
    rerun = tools.reestimate(view["session_id"], Reestimate(state_revision=view["state_revision"], input=provisional, item_ids=[first.item_id]))
    assert rerun["impact"]["estimates"][0]["item_ids"] == [first.item_id, second.item_id]
    assert len(rerun["work_rows"]) == 1
    assert rerun["impact"]["estimates"][0]["totals"]["md"] == view["impact"]["estimates"][0]["totals"]["md"]


def test_document_generation_rejects_concurrent_calculation(tools, provisional):
    from app.llm.base import RawResponse
    view = tools.estimate_impact(provisional)
    class RacingLlm(FakeLlm):
        def _call(self, **kwargs):
            tools.recalculate(view["session_id"], Recalculate(state_revision=view["state_revision"],
                edits=[{"work_id": "0:0", "md": 4.5}], reason="동시 수정"))
            return RawResponse(payload=copy.deepcopy(FAKE_EMAIL))
    tools.draft_llm = RacingLlm([])
    with pytest.raises(WorkflowConflict, match="stale"):
        documents(tools, view)
    assert not tools.get(view["session_id"])["documents"]


def test_missing_role_rates_keep_other_item_subtotals(provisional):
    second = provisional.items[0].model_copy(deep=True)
    second.item_id = provisional.context.case_id + "-2"
    second.intent = provisional.items[0].intent = "estimate_request"
    provisional.items.append(second)
    provisional.target_item_ids.append(second.item_id)
    add_verified_assessment(provisional, second.item_id)
    provisional.rate_card_id = "RC-SYNTH-2026H2-noQA"
    from app.agents.agent3.effort import load_effort_rules
    rules = load_effort_rules()["rules"]
    def rule(role):
        return next(k for k, v in rules.items() if v.get("kind") == "general_test" and v["role"] == role)
    payload = {"estimates": [{"item_numbers": [n], "status": "draft", "lines": [{"task": role,
        "item_numbers": [n], "role": role, "rule_id": rule(role)}]} for n, role in [(1, "BE"), (2, "QA")]], "questions": []}
    tools = Agent3Orchestration(LlmAgent3(FakeLlm([payload])))
    view = tools.estimate_impact(provisional)
    assert view["impact"]["estimates"][0]["totals"]
    assert view["impact"]["estimates"][1]["totals"] is None
    assert any(n["kind"] == "missing_standard" for n in view["needs"])
    assert not view["can_final_review"]


def test_reestimate_omitted_target_maintains_deferred_and_blocks_final_review(tools, provisional):
    view = tools.estimate_impact(provisional)
    replacement = provisional.model_copy(deep=True)
    replacement.target_item_ids = []
    view2 = tools.reestimate(view["session_id"], Reestimate(state_revision=view["state_revision"],
        input=replacement, item_ids=[provisional.items[0].item_id]))
    assert not view2["impact"]["estimates"]
    assert any(n["kind"] == "client_input" for n in view2["needs"])
    assert not view2["can_final_review"]


def test_reestimate_shared_question_expands_affected_closure(a3_input):
    from app.agents.agent3.effort import load_effort_rules
    rules = load_effort_rules()["rules"]
    be = next(k for k, r in rules.items() if r.get("kind") == "general_test" and r["role"] == "BE")
    raw = a3_input.model_dump(exclude={"pm_review"})
    raw["target_item_ids"] = [i.item_id for i in a3_input.targets()]
    p = ProvisionalAgent3Input.model_validate(raw)
    r2 = p.items[0].model_copy(deep=True)
    r2.item_id = p.context.case_id + "-2"
    p.items[0].intent = r2.intent = "estimate_request"
    p.items.append(r2)
    p.target_item_ids.append(r2.item_id)
    add_verified_assessment(p, r2.item_id)
    est = lambda n: {"item_numbers": [n], "status": "draft", "lines": [{"task": f"작업{n}", "item_numbers": [n], "role": "BE", "rule_id": be, "assumption": ""}]}
    first = {"estimates": [est(1), est(2)], "questions": [{"item_numbers": [1, 2], "field": "x", "text": "R1·R2 공통 필수 확인", "reason": "r", "target": "pm", "blocking": True}]}
    second = {"estimates": [est(1), est(2)], "questions": []}
    tools = Agent3Orchestration(LlmAgent3(FakeLlm([first, second])))
    v = tools.estimate_impact(p)
    assert any(q["text"] == "R1·R2 공통 필수 확인" for q in v["impact"]["questions"])
    v2 = tools.reestimate(v["session_id"], Reestimate(state_revision=v["state_revision"], input=p, item_ids=[p.items[0].item_id]))
    # Since question was shared across items, affected expanded to both items, requiring both to reestimate together.
    assert len(v2["impact"]["estimates"]) == 2


def test_older_request_revision_does_not_override_newer_current_session(tools, provisional):
    p1 = provisional.model_copy(deep=True)
    p2 = provisional.model_copy(deep=True)
    for x in (p2, p2.a2_result):
        x.context = x.context.model_copy(update={"request_revision": 2, "analysis_id": x.context.analysis_id + "-r2"})
    p2.a2_result.context = p2.context
    v_new = tools.estimate_impact(p2)
    v_old = tools.estimate_impact(p1)
    assert tools.get(v_new["session_id"])["current"] is True
    assert tools.get(v_old["session_id"])["current"] is False


def test_quote_links_answer_through_target_items(tools, provisional):
    # R1: change_request, R2: estimate_request pointing to R1
    quote = provisional.items[0].model_copy(deep=True)
    quote.item_id = provisional.context.case_id + "-quote"
    quote.intent = "estimate_request"
    quote.text = "견적도 같이 부탁드립니다."
    provisional.items.append(quote)
    provisional.target_item_ids.append(quote.item_id)
    provisional.quote_links = {quote.item_id: [provisional.items[0].item_id]}
    view = tools.estimate_impact(provisional)
    assert len(view["impact"]["estimates"]) == 1
    assert view["impact"]["estimates"][0]["item_ids"] == [provisional.items[0].item_id]
    # The quote item should be deferred with not_required (answered via target)
    assert any(n["kind"] == "not_required" and quote.item_id in n["item_ids"] for n in view["needs"])


@pytest.mark.parametrize("label", [None, "unclear", "in_scope", "approved_change"])
def test_quote_requests_always_require_verified_out_of_scope(tools, provisional, label):
    provisional.items[0].intent = "estimate_request"
    if label is None:
        provisional.a2_result.assessments = []
    else:
        assessment = provisional.a2_result.assessments[0]
        assessment.label = label
        if label == "unclear":
            assessment.evidence, assessment.evidence_level = [], "none"
            assessment.missing_info = ["계약 확인 필요"]
    view = tools.estimate_impact(provisional)
    assert not view["impact"]["estimates"]
    assert not tools.agent.llm.calls
    if label in (None, "unclear", "approved_change"):
        assert any(n["kind"] == "more_evidence" and n["target"] == "agent2" for n in view["needs"])


def test_question_identity_survives_reordering_and_distinguishes_same_field(provisional, a3_payload):
    first = copy.deepcopy(a3_payload)
    first["questions"] = [{"item_numbers": [1], "field": "implementation", "text": text,
        "reason": "확인 필요", "target": "client", "blocking": False}
        for text in ["사진 저장 방식을 알려주세요.", "구매자 인증 방식을 알려주세요."]]
    second = copy.deepcopy(first)
    second["questions"].reverse()
    service = Agent3Orchestration(LlmAgent3(FakeLlm([first, second])))
    view = service.estimate_impact(provisional)
    ids = {q["text"]: q["question_id"] for q in view["impact"]["questions"]}
    rerun = service.reestimate(view["session_id"], Reestimate(state_revision=view["state_revision"],
        input=provisional, item_ids=provisional.target_item_ids))
    assert ids == {q["text"]: q["question_id"] for q in rerun["impact"]["questions"]}
    assert len(set(ids.values())) == 2


def test_more_evidence_with_unestimated_work_never_creates_pm_question(provisional):
    payload = {"estimates": [{"item_numbers": [1], "status": "not_estimable", "lines": [],
        "reason": "계약 제외 범위 근거 부족"}], "questions": [],
        "needs": [{"kind": "more_evidence", "item_numbers": [1], "topic": "사진 저장 제외 조항"}]}
    service = Agent3Orchestration(LlmAgent3(FakeLlm([copy.deepcopy(payload)] * 2)))
    view = service.estimate_impact(provisional)
    assert view["phase"] == "WAITING_INPUT"
    assert view["impact"]["status"] == "NEEDS_INPUT"
    assert view["impact"]["questions"] == []
    assert [(n["kind"], n["target"], n["source"]) for n in view["needs"]] == [("more_evidence", "agent2", "agent3")]
    assert not view["can_final_review"]


def test_full_reestimate_does_not_mark_linked_quote_as_omitted(tools, provisional):
    quote = provisional.items[0].model_copy(deep=True)
    quote.item_id, quote.intent = provisional.context.case_id + "-quote", "estimate_request"
    provisional.items.append(quote)
    provisional.quote_links = {quote.item_id: [provisional.items[0].item_id]}
    # Only the actual change work is selected, not the separate quote instruction.
    view = tools.estimate_impact(provisional)
    view = tools.reestimate(view["session_id"], Reestimate(state_revision=view["state_revision"],
        input=provisional, item_ids=[i.item_id for i in provisional.items]))
    assert not any(n["kind"] == "client_input" for n in view["needs"])
    assert len(view["impact"]["estimates"]) == 1
    view = documents(tools, view)
    assert tools.review(view["session_id"], review(view))["phase"] == "FINAL_APPROVED"


def test_customer_document_filters_by_question_target_not_words(tools, provisional):
    from schema.schemas import Question
    other = provisional.items[0].model_copy(deep=True)
    other.item_id, other.intent = provisional.context.case_id + "-status", "status_check"
    provisional.items.append(other)
    provisional.a2_result.status = "NEEDS_INPUT"
    def question(id, target, text):
        return Question(question_id=id, case_id=provisional.context.case_id, stage="agent2",
            item_ids=[other.item_id], target=target, blocking=True, text=text, reason="대상 확인")
    provisional.a2_result.questions = [question("pm-q", "pm", "내부 결재 담당자를 지정해 주세요."),
        question("client-q", "client", "산정 대상 화면을 확인해주세요.")]
    view = tools.estimate_impact(provisional)
    routed = {n["question_id"]: n for n in view["needs"] if n.get("question_id")}
    assert routed["pm-q"]["target"] == "pm" and routed["pm-q"]["source"] == "agent2"
    assert routed["client-q"]["target"] == "client"
    view = documents(tools, view)
    text = view["documents"]["email"]["body"]
    assert "내부 결재 담당자" not in text
    assert "산정 대상 화면을 확인해주세요." in text
    assert view["phase"] == "WAITING_INPUT"


def test_model_customer_need_retains_routing_and_appears_in_draft(provisional, a3_payload):
    a3_payload["needs"] = [{"kind": "client_input", "item_numbers": [1],
        "topic": "고객 담당자를 지정해 주세요.", "target": "client"}]
    service = Agent3Orchestration(LlmAgent3(FakeLlm([a3_payload])), draft_llm=FakeLlm([FAKE_EMAIL]))
    view = service.estimate_impact(provisional)
    assert view["impact"]["questions"] == []
    assert view["needs"][0]["target"] == "client" and view["needs"][0]["source"] == "agent3"
    view = documents(service, view)
    assert "고객 담당자를 지정해 주세요." in view["documents"]["email"]["body"]
    assert not view["can_final_review"]
