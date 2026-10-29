"""② 도구 사용 에이전트 테스트. 가짜 LLM이 행동을 고르고, 코드의 울타리(기준선·본 섹션·가드)를 검사한다.

판단 성능이 아니라 루프·도구 실행·종료·울타리를 본다.
"""
import threading

import pytest
from schema.schemas import Agent2Input

from app.agents.agent2.agent import Cite, Judgment
from app.agents.agent2.tool_agent import AgentStep, ToolAgent2
from app.agents.base import AgentFailure
from app.llm.client import LlmResult, Usage
from test_agent2 import GOLD, INPUTS


def _judgment(a: dict, q: dict | None, evidence=None) -> Judgment:
    from app.agents.agent2.agent import QuestionDraft
    return Judgment(
        sufficient=True, next_queries=[], label=a["label"],
        evidence=evidence if evidence is not None else [
            Cite(doc_id=e["doc_id"], section_id=e["section_id"], quote=e["quote"], role=e["role"],
                 match_type=e["match_type"]) for e in a["evidence"]],
        rationale=a["rationale"], evidence_level=a["evidence_level"] or "none", missing_info=a["missing_info"],
        uncertainties=a["uncertainties"], approved_change_ref=a["approved_change_ref"],
        question=QuestionDraft(text=q["text"], reason=q["reason"], field=q["field"], target=q["target"],
                               blocking=q["blocking"]) if q else None)


class ScriptLLM:
    """항목마다 script(item_id, n) → 행동을 돌려준다. 기본 script: 검색 1번 → 정답 근거 섹션 읽기 → 정답 제출."""

    def __init__(self, gold: dict, script=None):
        self.by_item = {a["item_id"]: a for a in gold["assessments"]}
        self.qs = {i: q for q in gold["questions"] for i in q["item_ids"]}
        self.script = script or self.default
        self.n: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def default(self, item_id: str, n: int):
        a = self.by_item[item_id]
        reads = [AgentStep(thought="원문 확인", action="get_section", doc_id=e["doc_id"], section_id=e["section_id"])
                 for e in a["evidence"]]
        steps = [AgentStep(thought="관련 조항 검색", action="search_docs", query=a["rationale"][:20])] + reads
        if n < len(steps):
            return steps[n]
        return AgentStep(thought="충분", action="submit_judgment", judgment=_judgment(a, self.qs.get(item_id)))

    def complete_structured(self, *, system, user, schema, tool_name="submit", max_tokens=None, temperature=None):
        item_id = user.split('"item_id": "')[1].split('"')[0]
        with self._lock:
            n = self.n.get(item_id, 0)
            self.n[item_id] = n + 1
            self.calls.append((item_id, schema.__name__))
        if schema is Judgment:                           # 상한 도달 후 강제 판단
            parsed = _judgment(self.by_item[item_id], self.qs.get(item_id))
        else:
            parsed = self.script(item_id, n)
        return LlmResult[schema](parsed=parsed, usage=Usage(input_tokens=10, output_tokens=5),
                                 latency_ms=1, provider="fake", model_id="script", retried=False)


def _run(case_id: str, script=None, **kw):
    llm = ScriptLLM(GOLD[case_id], script)
    return ToolAgent2(llm, **kw).run(Agent2Input.model_validate(INPUTS[case_id])), llm


@pytest.mark.parametrize("case_id", sorted(GOLD))
def test_agent_reproduces_gold_when_it_reads_the_evidence(case_id):
    out, _ = _run(case_id)
    gold = GOLD[case_id]
    assert out.status == gold["status"]
    assert [a.label for a in out.assessments] == [a["label"] for a in gold["assessments"]]
    assert all(e.verified and e.locator.startswith("chunk:") for a in out.assessments for e in a.evidence)
    assert out.meta.agent_version.startswith("a2-agent")


def test_actions_are_chosen_by_llm_and_recorded():
    out, _ = _run("CR-A-001")
    acts = out.meta.debug["actions"]["CR-A-001-1"]
    assert acts[0] == "search_docs" and "get_section" in acts and acts[-1] == "submit_judgment"


def test_unseen_section_cannot_be_cited():
    """읽지 않은 섹션을 인용하면 코드가 버리고, 근거가 없으면 unclear."""
    gold = GOLD["CR-A-001"]

    def lazy(item_id, n):                               # 아무것도 안 보고 바로 정답 제출
        a = {x["item_id"]: x for x in gold["assessments"]}[item_id]
        return AgentStep(thought="바로 제출", action="submit_judgment", judgment=_judgment(a, None))
    out, _ = _run("CR-A-001", lazy)
    assert all(a.label == "unclear" and not a.evidence for a in out.assessments)


def test_other_project_document_is_refused():
    seen_results = []

    def snoop(item_id, n):
        if n == 0:
            return AgentStep(thought="다른 프로젝트", action="get_section", doc_id="FSP-B-001-v1.0", section_id="F-01")
        a = {x["item_id"]: x for x in GOLD["CR-A-001"]["assessments"]}[item_id]
        cite = [Cite(doc_id="FSP-B-001-v1.0", section_id="F-01", quote="x", role="basis", match_type="direct")]
        return AgentStep(thought="제출", action="submit_judgment", judgment=_judgment(a, None, evidence=cite))
    out, _ = _run("CR-A-001", snoop)
    assert all(a.label == "unclear" for a in out.assessments)
    assert not any(e.doc_id.startswith("FSP-B") for a in out.assessments for e in a.evidence)


def test_bad_tool_args_are_returned_as_observation_not_crash():
    def clumsy(item_id, n):
        if n == 0:
            return AgentStep(thought="오타", action="get_section", doc_id="FSP-A-001-v1.0", section_id="F-999")
        return ScriptLLM(GOLD["CR-A-001"]).default(item_id, n - 1)
    out, llm = _run("CR-A-001", clumsy)
    assert [a.label for a in out.assessments] == [a["label"] for a in GOLD["CR-A-001"]["assessments"]]


def test_step_limit_forces_final_judgment():
    def wander(item_id, n):
        return AgentStep(thought="계속 검색", action="search_docs", query=f"리뷰 {n}")
    out, llm = _run("CR-A-001", wander)
    per_item = [s for i, s in llm.calls if i == "CR-A-001-1"]
    assert per_item.count("AgentStep") == ToolAgent2.max_steps and per_item[-1] == "Judgment"
    assert out.assessments[0].label in {"out_of_scope", "unclear"}   # 검색으로 본 섹션만 근거가 된다


def test_search_failure_is_retrieval_unavailable(monkeypatch):
    import app.agents.agent2.tool_agent as mod

    def boom(*a, **kw):
        raise RuntimeError("index down")
    monkeypatch.setattr(mod, "search_docs", boom)
    with pytest.raises(AgentFailure) as e:
        _run("CR-A-001")
    assert e.value.error.code == "RETRIEVAL_UNAVAILABLE"


def test_hints_disabled_are_not_shown():
    case_id = next(c for c, i in INPUTS.items() if any(it["feature_candidates"] for it in i["items"]))
    llm = ScriptLLM(GOLD[case_id])
    seen_users = []
    orig = llm.complete_structured

    def spy(**kw):
        seen_users.append(kw["user"])
        return orig(**kw)
    llm.complete_structured = spy
    ToolAgent2(llm, use_hints=False).run(Agent2Input.model_validate(INPUTS[case_id]))
    assert all('"feature_hints": []' in u for u in seen_users)


def test_registry_builds_tool_agent_by_default(monkeypatch):
    from app.agents import registry
    from app.core.config import settings
    monkeypatch.setattr(settings, "agent2_style", "agent")
    assert isinstance(registry.build_agent2(ScriptLLM(GOLD["CR-A-001"])), ToolAgent2)
    monkeypatch.setattr(settings, "agent2_style", "workflow")
    assert not isinstance(registry.build_agent2(ScriptLLM(GOLD["CR-A-001"])), ToolAgent2)
