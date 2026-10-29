"""② 도구 사용 에이전트 테스트. 가짜 LLM이 행동을 고르고, 코드의 울타리(기준선·본 섹션·가드)를 검사한다.

판단 성능이 아니라 루프·도구 실행·종료·울타리를 본다.
"""
import threading

import pytest
from schema.schemas import Agent2Input

from app.agents.agent2.agent import Cite, Judgment
from app.agents.agent2.tool_agent import AgentStep, ToolAgent2
from app.agents.base import AgentFailure
from app.kb.loader import sections
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


def _spy_users(case_id: str, script=None):
    llm = ScriptLLM(GOLD[case_id], script)
    users: list[str] = []
    orig = llm.complete_structured

    def spy(**kw):
        users.append(kw["user"])
        return orig(**kw)
    llm.complete_structured = spy
    out = ToolAgent2(llm).run(Agent2Input.model_validate(INPUTS[case_id]))
    return out, users


def test_first_observation_lists_contract_sections():
    _, users = _spy_users("CR-A-001")
    first = users[0]
    assert "<contract_sections>" in first and '"section_id":"C-06"' in first   # 하자 조항 제목이 처음부터 보인다
    assert "<read_sections>\n[]" in first                                       # 아직 읽은 전문은 없다


def test_contract_index_alone_does_not_allow_citation():
    """목록에 제목만 본 조항은 근거가 될 수 없다 — 읽어야 한다."""
    def cite_index_only(item_id, n):
        a = {x["item_id"]: x for x in GOLD["CR-A-001"]["assessments"]}[item_id]
        cite = [Cite(doc_id="CTR-A-001-v1.0", section_id="C-06", quote="하자", role="policy", match_type="direct")]
        return AgentStep(thought="목록만 보고 제출", action="submit_judgment", judgment=_judgment(a, None, evidence=cite))
    out, _ = _run("CR-A-001", cite_index_only)
    assert not any(e.section_id == "C-06" for a in out.assessments for e in a.evidence)


def test_read_section_text_is_sent_once_and_reread_is_noted():
    a = GOLD["CR-A-001"]["assessments"][0]
    ev = a["evidence"][0]
    text = sections()[(ev["doc_id"], ev["section_id"])]["text"]

    def reread(item_id, n):
        if n < 3:                                        # 같은 섹션을 세 번 읽으려 한다
            return AgentStep(thought="읽기", action="get_section", doc_id=ev["doc_id"], section_id=ev["section_id"])
        return ScriptLLM(GOLD["CR-A-001"]).default(item_id, 0) if n == 3 else \
            AgentStep(thought="제출", action="submit_judgment", judgment=_judgment(a, None))
    out, users = _spy_users("CR-A-001", reread)
    assert all(u.count(text) <= 1 for u in users)                # 전문은 관찰마다 최대 한 번
    assert "이미 읽음" in users[3]


def test_search_omits_snippet_for_already_read_sections():
    a = GOLD["CR-A-001"]["assessments"][0]
    ev = a["evidence"][0]
    agent = ToolAgent2(ScriptLLM(GOLD["CR-A-001"]))
    allowed = agent._baseline_docs("P-COM-101:BL-2")
    seen, read = [], {}
    agent._run_tool(AgentStep(thought="", action="get_section", doc_id=ev["doc_id"], section_id=ev["section_id"]),
                    "P-COM-101:BL-2", allowed, seen, read)
    res = agent._run_tool(AgentStep(thought="", action="search_docs", query=sections()[(ev["doc_id"], ev["section_id"])]["heading"]),
                          "P-COM-101:BL-2", allowed, seen, read)["results"]
    hit = [r for r in res if (r["doc_id"], r["section_id"]) == (ev["doc_id"], ev["section_id"])]
    assert hit and hit[0].get("read") is True and "snippet" not in hit[0]


def test_precheck_pushes_back_once_when_hint_unread():
    """힌트 조항을 안 보고 제출하면 한 번 되돌린다. 두 번째 제출은 받는다 (무한 반복 방지)."""
    case_id = next(c for c, i in INPUTS.items() if any(it["feature_candidates"] for it in i["items"]))
    gold = GOLD[case_id]

    def eager(item_id, n):
        a = {x["item_id"]: x for x in gold["assessments"]}[item_id]
        return AgentStep(thought="바로 제출", action="submit_judgment", judgment=_judgment(a, None))
    out, llm = _run(case_id, eager)
    for item_id, steps in out.meta.debug["steps"].items():
        if any(it["feature_candidates"] for it in INPUTS[case_id]["items"] if it["item_id"] == item_id):
            assert steps[0].get("rejected") and "기능 후보" in steps[0]["gaps"][0]
            assert steps[-1] == {"action": "submit_judgment"} and len(steps) == 2


def test_precheck_requires_defect_clause_for_defect_items():
    case_id = next(c for c, i in INPUTS.items() if any(it["intent"] == "defect" for it in i["items"]))
    item = next(it for it in INPUTS[case_id]["items"] if it["intent"] == "defect")
    from schema.schemas import Item
    agent = ToolAgent2(ScriptLLM(GOLD[case_id]), use_hints=False)
    allowed = agent._baseline_docs(INPUTS[case_id]["context"]["baseline_id"])
    gaps = agent._precheck(Item.model_validate(item), [], [], {}, allowed)
    assert any("하자" in g for g in gaps)
    defect = next(k for k, s in sections().items() if k[0] in allowed and s["doc_type"] == "contract" and "하자" in s["heading"])
    assert agent._precheck(Item.model_validate(item), [], [defect], {defect: {}}, allowed) == []


def test_steps_trace_records_queries_and_hits():
    out, _ = _run("CR-A-001")
    steps = out.meta.debug["steps"]["CR-A-001-1"]
    search = next(s for s in steps if s["action"] == "search_docs")
    assert search["query"] and search["hits"]


def _flaky(code: str, retryable: bool, fails: int):
    from schema.schemas import AgentError
    llm = ScriptLLM(GOLD["CR-A-001"])
    orig, n = llm.complete_structured, {"left": fails}

    def call(**kw):
        if n["left"] > 0:
            n["left"] -= 1
            raise AgentFailure(AgentError(code=code, message="x", stage=None, retryable=retryable))
        return orig(**kw)
    llm.complete_structured = call
    return llm, n


def test_rate_limit_is_retried_with_backoff(monkeypatch):
    import app.agents.agent2.agent as mod
    slept = []
    monkeypatch.setattr(mod.time, "sleep", slept.append)
    llm, _ = _flaky("MODEL_TIMEOUT", True, 2)
    out = ToolAgent2(llm).run(Agent2Input.model_validate(INPUTS["CR-A-001"]))
    assert out.meta.debug["llm_retries"] == 2 and len(slept) == 2 and slept[1] > slept[0]


def test_retries_are_bounded(monkeypatch):
    import app.agents.agent2.agent as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    llm, _ = _flaky("MODEL_TIMEOUT", True, 99)
    with pytest.raises(AgentFailure) as e:
        ToolAgent2(llm).run(Agent2Input.model_validate(INPUTS["CR-A-001"]))
    assert e.value.error.code == "MODEL_TIMEOUT" and e.value.error.stage == "agent2"


@pytest.mark.parametrize("code,retryable", [("INTERNAL_ERROR", False), ("OUTPUT_INVALID", True)])
def test_non_rate_limit_errors_are_not_retried(monkeypatch, code, retryable):
    import app.agents.agent2.agent as mod
    slept = []
    monkeypatch.setattr(mod.time, "sleep", slept.append)
    llm, n = _flaky(code, retryable, 1)
    with pytest.raises(AgentFailure):
        ToolAgent2(llm).run(Agent2Input.model_validate(INPUTS["CR-A-001"]))
    assert slept == []
