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


@pytest.mark.parametrize("facts,expected", [([], "unclear"), (["원문에 없는 로그 내용"], "unclear"), (None, "in_scope")])
def test_issue_report_in_scope_needs_fact_quoted_from_message(facts, expected):
    """원인 불명 제보를 in_scope로 단정하려면 원문의 사실을 인용해야 한다 (None = 원문 첫 문장 인용)."""
    from schema.schemas import Item
    case_id = next(c for c, i in INPUTS.items() if any(it["intent"] == "issue_report" for it in i["items"]))
    inp = INPUTS[case_id]
    item = Item.model_validate(next(it for it in inp["items"] if it["intent"] == "issue_report"))
    a = GOLD[case_id]["assessments"][0]
    keys = [(e["doc_id"], e["section_id"]) for e in a["evidence"]]
    quoted = [inp["message_text"][:20]] if facts is None else facts
    j = Judgment(sufficient=True, next_queries=[], label="in_scope",
                 evidence=[Cite(doc_id=d, section_id=s, quote="x", role="basis", match_type="direct") for d, s in keys],
                 rationale="r", evidence_level="medium", missing_info=[], uncertainties=[], approved_change_ref=None,
                 question=None, defect_facts=quoted)
    out = LlmAgent2._to_assessment(item, j, keys, inp["message_text"])
    assert out.label == expected
    if expected == "unclear":
        assert any("결함으로 볼 사실" in m for m in out.missing_info)
