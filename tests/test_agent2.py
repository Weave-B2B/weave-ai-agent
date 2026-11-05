"""② LLM 에이전트 파이프라인 테스트. LLM은 정답을 흉내 내는 가짜로 바꿔 끼운다 (API 키 불필요).

여기서 보는 것은 검색·후보 구성·Evidence 조립·질문·status·가드 연결이지 판단 성능이 아니다.
"""
import pytest
from conftest import split_rows
from schema.schemas import Agent2Input

from app.agents.agent2.agent import Cite, Judgment, LlmAgent2, QuestionDraft, SearchPlan
from app.agents.base import AgentFailure
from app.llm.client import LlmResult, Usage

GOLD = {g["context"]["case_id"]: g for g in split_rows("agent2", "gold")}
INPUTS = {i["context"]["case_id"]: i for i in split_rows("agent2", "input")}


class GoldLLM:
    """판단 호출에 정답 assessment를 돌려준다. 첫 판단은 sufficient=False로 재검색 경로를 한 번 탄다."""

    def __init__(self, gold: dict, quote_override: str | None = None):
        self.by_item = {a["item_id"]: a for a in gold["assessments"]}
        self.qs = {i: q for q in gold["questions"] for i in q["item_ids"]}
        self.quote_override = quote_override
        self.judge_calls = 0

    def complete_structured(self, *, system, user, schema, tool_name="submit", max_tokens=None, temperature=None):
        return LlmResult[schema](parsed=self._parsed(user, schema), usage=Usage(input_tokens=10, output_tokens=5),
                                 latency_ms=1, provider="fake", model_id="gold", retried=False)

    def _parsed(self, user, schema):
        if schema is SearchPlan:
            return SearchPlan(queries=["기능 범위"], doc_types=[])
        self.judge_calls += 1
        item_id = user.split('"item_id": "')[1].split('"')[0]
        a, q = self.by_item[item_id], self.qs.get(item_id)
        return Judgment(
            sufficient=self.judge_calls > 1, next_queries=["다른 표현"], label=a["label"],
            evidence=[Cite(doc_id=e["doc_id"], section_id=e["section_id"], quote=self.quote_override or e["quote"],
                           role=e["role"], match_type=e["match_type"]) for e in a["evidence"]],
            rationale=a["rationale"], evidence_level=a["evidence_level"] or "none", missing_info=a["missing_info"],
            uncertainties=a["uncertainties"], approved_change_ref=a["approved_change_ref"],
            question=QuestionDraft(text=q["text"], reason=q["reason"], field=q["field"], target=q["target"],
                                   blocking=q["blocking"]) if q else None)


def _run(case_id: str, **kw):
    inp = Agent2Input.model_validate(INPUTS[case_id])
    llm = GoldLLM(GOLD[case_id], **kw)
    return LlmAgent2(llm).run(inp), llm


@pytest.mark.parametrize("case_id", sorted(GOLD))
def test_pipeline_reproduces_gold_labels(case_id):
    out, _ = _run(case_id)
    gold = GOLD[case_id]
    assert out.status == gold["status"]
    assert [a.label for a in out.assessments] == [a["label"] for a in gold["assessments"]]
    for a in out.assessments:
        assert all(e.verified for e in a.evidence)


def test_research_round_runs_when_insufficient():
    case_id = next(c for c, g in GOLD.items() if len(g["assessments"]) == 1)
    _, llm = _run(case_id)
    assert llm.judge_calls == 2


def test_hallucinated_quote_becomes_unclear():
    case_id = next(c for c, g in GOLD.items() if g["assessments"][0]["label"] == "out_of_scope")
    out, _ = _run(case_id, quote_override="원문에 없는 문장입니다")
    assert all(a.label == "unclear" and a.missing_info for a in out.assessments)


def test_search_failure_is_retrieval_unavailable(monkeypatch):
    import app.agents.agent2.agent as mod

    def boom(*a, **kw):
        raise RuntimeError("index down")
    monkeypatch.setattr(mod, "search_docs", boom)
    case_id = sorted(GOLD)[0]
    with pytest.raises(AgentFailure) as e:
        _run(case_id)
    assert e.value.error.code == "RETRIEVAL_UNAVAILABLE"


def test_approved_change_without_change_order_evidence_becomes_unclear():
    """다른 기능만 승인한 변경합의서로 넓히지 않는다 — 근거에 변경합의서가 없으면 approved_change 불가."""
    out, _ = _run("CR-A-006")
    assert out.assessments[0].label == "approved_change"

    class NoCO(GoldLLM):
        def _parsed(self, user, schema):
            p = super()._parsed(user, schema)
            if isinstance(p, Judgment):
                p.evidence = [c for c in p.evidence if not c.doc_id.startswith("CO-")]
                assert p.evidence                        # 기능명세 근거는 남는다 — 가드가 내려야 한다
            return p
    inp = Agent2Input.model_validate(INPUTS["CR-A-006"])
    a = LlmAgent2(NoCO(GOLD["CR-A-006"])).run(inp).assessments[0]
    assert a.label == "unclear" and a.approved_change_ref is None and a.missing_info


def test_question_basis_refs_keep_only_verified_evidence():
    out, _ = _run("CR-A-005")
    assert out.questions and out.questions[0].basis_refs
    out, _ = _run("CR-A-005", quote_override="원문에 없는 문장입니다")
    assert out.questions and all(q.basis_refs == [] for q in out.questions)


def test_hint_usage_is_recorded_and_can_be_disabled():
    case_id = next(c for c, i in INPUTS.items() if any(it["feature_candidates"] for it in i["items"]))
    inp = Agent2Input.model_validate(INPUTS[case_id])
    on = LlmAgent2(GoldLLM(GOLD[case_id]), use_hints=True).run(inp).meta.debug["hints"]
    off = LlmAgent2(GoldLLM(GOLD[case_id]), use_hints=False).run(inp).meta.debug["hints"]
    assert on["enabled"] and any(h["given"] for h in on["items"].values())
    assert not off["enabled"] and all(h["given"] == [] for h in off["items"].values())


def test_search_results_survive_candidate_cap(monkeypatch):
    """기본 후보(계약서·변경합의서)가 많아도 새 검색 결과가 잘리지 않는다."""
    import app.agents.agent2.agent as mod
    seen: list[str] = []

    class Spy(GoldLLM):
        def _parsed(self, user, schema):
            if schema is Judgment:
                seen.append(user)
            return super()._parsed(user, schema)
    monkeypatch.setattr(mod, "search_docs", lambda q, b, top_k, filters=None:
                        [{"doc_id": "FSP-A-001-v1.0", "section_id": "F-11"}])
    inp = Agent2Input.model_validate(INPUTS["CR-A-001"])
    LlmAgent2(Spy(GOLD["CR-A-001"]), use_hints=False).run(inp)
    assert all('"section_id": "F-11"' in u for u in seen)


def test_unknown_baseline_is_input_error():
    from app.tools.errors import ToolInputError
    raw = {**INPUTS["CR-A-001"], "context": {**INPUTS["CR-A-001"]["context"], "baseline_id": "P-COM-101:BL-9"}}
    with pytest.raises(ToolInputError):
        LlmAgent2(GoldLLM(GOLD["CR-A-001"])).run(Agent2Input.model_validate(raw))


def _issue_report_judgment(case_id: str, question=None):
    from schema.schemas import Item
    inp = INPUTS[case_id]
    item = Item.model_validate(next(it for it in inp["items"] if it["intent"] == "issue_report"))
    a = GOLD[case_id]["assessments"][0]
    keys = [(e["doc_id"], e["section_id"]) for e in a["evidence"]]
    j = Judgment(sufficient=True, next_queries=[], label="in_scope",
                 evidence=[Cite(doc_id=d, section_id=s, quote="x", role="basis", match_type="direct") for d, s in keys],
                 rationale="r", evidence_level="medium", missing_info=[], uncertainties=[], approved_change_ref=None,
                 question=question)
    return item, j, keys


ISSUE_CASE = next(c for c, i in INPUTS.items() if any(it["intent"] == "issue_report" for it in i["items"]))


def test_issue_report_is_held_until_answers_arrive():
    """원인 불명 제보는 답변 전에는 in_scope로 확정하지 않고, 고객 질문을 붙인다."""
    item, j, keys = _issue_report_judgment(ISSUE_CASE)
    held = LlmAgent2._hold_issue_report(item, j, has_answers=False)
    a = LlmAgent2._to_assessment(item, held, keys)
    q = LlmAgent2._to_question(item, held, ISSUE_CASE)
    assert a.label == "unclear" and any("원인 불명" in m for m in a.missing_info)
    assert q and q.target == "client" and q.blocking is False


def test_issue_report_can_be_in_scope_after_answers():
    item, j, _ = _issue_report_judgment(ISSUE_CASE)
    assert LlmAgent2._hold_issue_report(item, j, has_answers=True).label == "in_scope"


def test_issue_report_hold_keeps_llm_question():
    mine = QuestionDraft(text="p95 측정 결과를 주세요", reason="r", field="p95", target="client", blocking=False)
    item, j, _ = _issue_report_judgment(ISSUE_CASE, question=mine)
    assert LlmAgent2._hold_issue_report(item, j, has_answers=False).question.text == "p95 측정 결과를 주세요"


def _confirmation(label: str, answer: str | None = "기능명세 3.2에 포함되어 있습니다."):
    from schema.schemas import Item
    a = GOLD["CR-A-001"]["assessments"][0]
    keys = [(e["doc_id"], e["section_id"]) for e in a["evidence"]]
    raw = {**INPUTS["CR-A-001"]["items"][0], "intent": "confirmation"}
    j = Judgment(sufficient=True, next_queries=[], label=label,
                 evidence=[Cite(doc_id=e["doc_id"], section_id=e["section_id"], quote=e["quote"], role=e["role"],
                                match_type=e["match_type"]) for e in a["evidence"]],
                 rationale="r", evidence_level="strong", missing_info=[], uncertainties=[], approved_change_ref=None,
                 question=None, answer=answer)
    return Item.model_validate(raw), j, keys


def test_confirmation_answer_is_kept_with_a_judgment():
    item, j, keys = _confirmation("in_scope")
    assert LlmAgent2._to_assessment(item, j, keys).confirmation_answer == j.answer


def test_confirmation_answer_is_dropped_when_held_or_not_confirmation():
    item, j, keys = _confirmation("unclear")
    assert LlmAgent2._to_assessment(item, j, keys).confirmation_answer is None
    item, j, keys = _confirmation("in_scope")
    change = item.model_copy(update={"intent": "change_request"})
    assert LlmAgent2._to_assessment(change, j, keys).confirmation_answer is None


def test_guard_drops_confirmation_answer_when_evidence_fails():
    from app.agents.agent2.guards import apply_guards
    out, _ = _run("CR-A-001", quote_override="문서에 없는 문장")
    data = out.model_dump()
    for a in data["assessments"]:
        a.update(label="in_scope", evidence_level="strong", confirmation_answer="포함되어 있습니다.",
                 evidence=[{**e, "quote": "문서에 없는 문장"} for e in GOLD["CR-A-001"]["assessments"][0]["evidence"]]
                 if not a["evidence"] else a["evidence"])
    guarded = apply_guards(type(out).model_validate(data))
    assert all(a.label == "unclear" and a.confirmation_answer is None for a in guarded.assessments)


def test_conditional_followup_reaches_the_model():
    raw = {**INPUTS["CR-A-001"], "items": [{**INPUTS["CR-A-001"]["items"][0], "intent": "confirmation",
                                            "conditional_followup": "범위 밖이면 추가 희망"}]}
    llm = GoldLLM(GOLD["CR-A-001"])
    seen = []
    orig = llm._parsed
    llm._parsed = lambda user, schema: (seen.append(user), orig(user, schema))[1]
    LlmAgent2(llm).run(Agent2Input.model_validate(raw))
    assert any("범위 밖이면 추가 희망" in u for u in seen)


def test_question_id_differs_by_content_and_answers_but_is_stable():
    from schema.schemas import Answer, Question
    q = Question(question_id="", case_id="C", stage="agent2", item_ids=["C-1"], field="facts", text="재현 조건은?",
                 reason="r", target="client")
    other = q.model_copy(update={"text": "로그를 주실 수 있나요?"})
    ans = [Answer(answer_id="A1", question_id="x", text="t", answered_by="pm", answered_at="2026-11-05T10:00:00+09:00")]
    qid = LlmAgent2._question_id
    assert qid("C", q, []) == qid("C", q, []) and qid("C", q, []).startswith("C-1-A2Q-")
    assert qid("C", q, []) != qid("C", other, [])                      # 재실행 때 새 질문이 이전 질문 ID로 덮이지 않는다
    assert qid("C", q, []) != qid("C", q, ans)                         # 답변 후 다시 물으면 새 질문


def test_issue_report_hold_ignores_answers_to_other_items_questions():
    """원인 불명 제보는 다른 항목의 Agent 2 질문에 답이 와도 확정하지 않는다."""
    from schema.schemas import Answer
    item, j, keys = _issue_report_judgment(ISSUE_CASE)
    ans = lambda qid: [Answer(answer_id="A1", question_id=qid, text="t", answered_by="client",
                              answered_at="2026-11-05T10:00:00+09:00")]
    assert not LlmAgent2._answered(item, ans(f"{item.item_id}0-A2Q-abc"))   # 다른 항목(ID 앞부분만 같은)
    assert not LlmAgent2._answered(item, ans("OTHER-1-A2Q-abc"))
    assert LlmAgent2._answered(item, ans(f"{item.item_id}-A2Q-abc"))
    assert LlmAgent2._answered(item, ans(f"{ISSUE_CASE}-Q1"))                 # Agent 1 질문 답변은 관련 있을 수 있음
    held = LlmAgent2._hold_issue_report(item, j, LlmAgent2._answered(item, ans("OTHER-1-A2Q-abc")))
    assert held.label == "unclear"


@pytest.mark.parametrize("exc", [KeyError("bug"), __import__("app.embed", fromlist=["x"]).EmbedConfigError("no key")])
def test_config_errors_and_bugs_are_not_retrieval_outages(monkeypatch, exc):
    import app.agents.agent2.agent as mod

    def boom(*a, **kw):
        raise exc
    monkeypatch.setattr(mod, "search_docs", boom)
    with pytest.raises(type(exc)):
        _run("CR-A-001")


def test_change_order_ref_without_version_matches_only_that_document(monkeypatch):
    import app.agents.agent2.agent as mod
    docs = {"CO-A-0010-v1.0": {}, "CO-A-001-v1.0": {}}
    monkeypatch.setattr(mod, "documents", lambda: docs)
    item, j, keys = _confirmation("approved_change")
    j = j.model_copy(update={"approved_change_ref": "CO-A-001"})
    assert LlmAgent2._to_assessment(item, j, keys).approved_change_ref == "CO-A-001-v1.0"
