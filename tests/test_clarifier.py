"""총괄 Clarifier: gap 합치기·질문/가정 선별·근거 검증·되풀이 방지."""
import json
from types import SimpleNamespace

import pytest

from app.agents.gaps import gaps_from_output, make_gap_id
from app.orchestration.clarifier import Clarifier, ClarifierDraft, ClarifierSearchPlan, is_question_sentence, merge_gaps
from app.orchestration.run_coordination import CoordinationState, PMQuestion
from schema.schemas import InfoGap, Question


class StubLlm:
    """질문 작성 호출은 calls, 검색어 결정 호출은 plan_calls에 따로 기록한다."""
    def __init__(self, draft=None, error=None, plan=None):
        self.draft, self.error, self.plan, self.calls, self.plan_calls = draft, error, plan, [], []

    def complete_structured(self, **kw):
        if kw["schema"] is ClarifierSearchPlan:
            self.plan_calls.append(kw)
            if self.error or self.plan is None:
                raise self.error or RuntimeError("검색어 미제공")
            return SimpleNamespace(parsed=ClarifierSearchPlan.model_validate(self.plan))
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(parsed=ClarifierDraft.model_validate(self.draft))


def gap(stage="agent1", items=("R1",), slot="deadline_fixed", missing="12월 말 검수일은 확정 일정인가요?", **kw):
    return InfoGap(gap_id=make_gap_id(stage, list(items), slot), stage=stage, item_ids=list(items), slot=slot,
                   missing=missing, impact=kw.pop("impact", "일정 산정에 필요"), **kw)


SEARCH = lambda q: [{"doc_id": "SOW-1", "section_id": "4.2", "text": "최종 검수는 12월 31일까지 완료한다."}]
CONTEXT = lambda: {"message_text": "원문", "doc_titles": {"SOW-1": "과업지시서"}}


def run(clarifier, gaps, coord=None):
    return clarifier.reconcile(coord or CoordinationState(), gaps, item_text={"R1": "맛집 정보 추가"},
                               evidence_context=CONTEXT)


# ---- gap 만들기 ----
def test_gaps_from_questions_unclear_scope_and_agent3_needs():
    q = Question(question_id="A1Q1", case_id="C", stage="agent1", item_ids=["R1"], field="Photo Limit",
                 text="사진은 몇 장인가요?", reason="수량", target="pm", blocking=True, choices=["3장", "5장"],
                 assumption_if_unknown="3장으로 가정")
    g = gaps_from_output("agent1", SimpleNamespace(gaps=[], questions=[q]))[0]
    assert (g.slot, g.blocking, g.question_id, g.suggested_options, g.assumption_if_unknown) == (
        "photo_limit", True, "A1Q1", ["3장", "5장"], "3장으로 가정")

    a2 = SimpleNamespace(gaps=[], questions=[], assessments=[SimpleNamespace(
        item_id="R2", label="unclear", missing_info=["재사용 여부"], rationale="근거 부족", evidence=[])])
    scope = gaps_from_output("agent2", a2)[0]
    assert (scope.slot, scope.blocking, scope.resolvable_by) == ("scope_missing", True, ["docs", "pm"])

    needs = [SimpleNamespace(kind="client_input", item_ids=["R1"], topic="맛집 수량", target="client", blocking=False,
                             question_id=None, reason="", choices=["3곳"], assumption_if_unknown="3곳으로 가정"),
             SimpleNamespace(kind="more_evidence", item_ids=["R1"], topic="계약 조항", target="agent2", blocking=True,
                             question_id=None)]
    a3 = gaps_from_output("agent3", SimpleNamespace(gaps=[], questions=[], needs=needs))
    assert [(g.target, g.blocking, g.suggested_options) for g in a3] == [("client", False, ["3곳"])]


def test_existing_gaps_are_used_as_reported():
    reported = [gap()]
    assert gaps_from_output("agent1", SimpleNamespace(gaps=reported, questions=[])) == reported


# ---- 합치기 ----
def test_same_information_from_different_agents_is_merged():
    merged = merge_gaps([gap(stage="agent3", blocking=True), gap(stage="agent1", blocking=False),
                         gap(stage="agent2", slot="other_name", missing="12월 말 검수일은 확정 일정인가요 ?")])
    assert len(merged) == 1
    m = merged[0]
    assert (m.stage, m.blocking, len(m.gap_ids)) == ("agent1", True, 3)


def test_question_sentence_check_rejects_review_requests():
    assert is_question_sentence("검수일은 확정 일정인가요?")
    assert not is_question_sentence("반영 가능 여부와 일정 영향을 검토해 주세요.")
    assert not is_question_sentence("일정 영향을 검토해 주세요?")


# ---- 선별 ----
def test_non_blocking_gap_becomes_assumption_without_model_call():
    llm = StubLlm()
    coord = run(Clarifier(llm, search=SEARCH), [gap(blocking=False, assumption_if_unknown="검수일은 확정으로 가정")])
    assert not coord.questions and not llm.calls
    assert [(a.kind, a.text) for a in coord.assumptions] == [("unasked", "검수일은 확정으로 가정")]


# ---- 작성·검증 ----
def test_blocking_gap_is_written_with_grounded_choices_only():
    g = gap(blocking=True, suggested_options=["협의해 조정 가능"])
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": "12월 말 검수일은 변경할 수 없는 확정 일정인가요?",
        "choices": [{"label": "확정 일정이며 변경 불가", "evidence_refs": ["SOW-1:4.2"]},
                    {"label": "근거 없는 지어낸 선택지", "evidence_refs": ["FAKE:9"]},
                    {"label": "협의해 조정 가능", "evidence_refs": []},
                    {"label": "모름", "evidence_refs": ["SOW-1:4.2"]}]}]})
    coord = run(Clarifier(llm, search=SEARCH), [g])
    q = coord.questions[0]
    assert q.text == "12월 말 검수일은 변경할 수 없는 확정 일정인가요?"
    assert q.choices == ["확정 일정이며 변경 불가", "협의해 조정 가능"]  # 근거·제안 없는 것, '모름'은 제외
    assert q.choice_evidence["확정 일정이며 변경 불가"][0].title == "과업지시서 · 4.2"
    assert (q.gap_ids, q.slot, q.stage) == ([g.gap_id], "deadline_fixed", "agent1")
    assert len(llm.calls) == 1


def test_review_request_from_model_falls_back_to_agent_question():
    g = gap(blocking=True, question_id="A3Q1")
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": "일정 영향을 검토해 주세요.", "choices": []}]})
    q = run(Clarifier(llm, search=SEARCH), [g]).questions[0]
    assert (q.question_id, q.text) == ("A3Q1", "12월 말 검수일은 확정 일정인가요?")


def test_model_failure_still_asks_with_agent_question():
    g = gap(blocking=True, suggested_options=["확정", "조정 가능"])
    q = run(Clarifier(StubLlm(error=RuntimeError("timeout")), search=SEARCH), [g]).questions[0]
    assert q.text == g.missing and q.choices == ["확정", "조정 가능"]


@pytest.mark.parametrize("refs, resolvable, asked", [
    (["SOW-1:4.2"], ["docs", "pm"], False),   # 문서가 답함 → 가정으로 기록
    (["FAKE:1"], ["docs", "pm"], True),       # 근거가 검색 결과에 없음 → 질문
    (["SOW-1:4.2"], ["pm"], True),            # 문서로 해결할 수 없는 정보 → 질문
])
def test_documents_can_resolve_a_gap_only_with_grounded_refs(refs, resolvable, asked):
    g = gap(blocking=True, resolvable_by=resolvable)
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "resolved", "question": "검수일은 확정인가요?",
                                  "resolved_answer": "최종 검수는 12월 31일까지입니다.", "resolved_refs": refs}]})
    coord = run(Clarifier(llm, search=SEARCH), [g])
    assert bool(coord.questions) is asked
    if not asked:
        a = coord.assumptions[0]
        assert (a.kind, a.evidence[0].source_type) == ("resolved", "contract")


# ---- 되풀이 방지·한도 ----
def test_answered_unknown_blocking_gap_holds_instead_of_asking_again():
    g = gap(blocking=True, question_id="A1Q1")
    prior = PMQuestion(question_id="A1Q1", item_ids=["R1"], field="deadline_fixed", text=g.missing,
                       status="resolved", answer="모름", disposition="unknown")
    llm = StubLlm()
    coord = run(Clarifier(llm, search=SEARCH), [g], CoordinationState(questions=[prior]))
    assert len(coord.questions) == 1 and not llm.calls
    assert "모름" in coord.hold_reason


def test_open_question_is_not_duplicated():
    g = gap(blocking=True)
    prior = PMQuestion(question_id="x", item_ids=["R1"], field="deadline_fixed", text="다른 표현의 질문?")
    coord = run(Clarifier(StubLlm(), search=SEARCH), [g], CoordinationState(questions=[prior]))
    assert len(coord.questions) == 1 and coord.hold_reason is None


def test_question_limit_holds():
    gaps = [gap(blocking=True, slot=f"s{i}", missing=f"정보 {i}는 무엇인가요?") for i in range(5)]
    coord = run(Clarifier(StubLlm(error=RuntimeError()), search=SEARCH), gaps)
    assert len(coord.questions) == 4 and "한도" in coord.hold_reason


# ---- 실제 모델 확인에서 나온 보완 ----
def test_single_fact_check_and_fallback_assumption_text():
    from app.orchestration.clarifier import is_single_fact, unasked_assumption_text
    assert is_single_fact("변경할 화면은 어디인가요?")
    assert not is_single_fact("변경 대상 화면은 무엇이며, 바꾸려는 결과는 각각 무엇인가요?")
    assert not is_single_fact("화면은 어디인가요? 결과는 무엇인가요?")
    coord = run(Clarifier(StubLlm(), search=SEARCH), [gap(blocking=False, missing="맛집은 몇 곳을 보여줄까요?")])
    assert coord.assumptions[0].text == "확인 전까지 미확정으로 두고 진행합니다 (확인할 내용: 맛집은 몇 곳을 보여줄까요?)"


def test_compound_model_question_falls_back_to_single_fact_agent_question():
    g = gap(blocking=True, question_id="A2Q", missing="변경할 화면은 어디인가요?")
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "choices": [],
                                  "question": "변경 대상 화면은 무엇이며, 결과는 각각 무엇인가요?"}]})
    assert run(Clarifier(llm, search=SEARCH), [g]).questions[0].text == "변경할 화면은 어디인가요?"


def test_model_marks_same_information_and_one_question_covers_both():
    first = gap(stage="agent1", slot="target_screen", missing="어느 화면을 바꾸나요?", blocking=True)
    second = gap(stage="agent2", slot="scope_target", missing="변경 대상 화면이 무엇인가요?", blocking=True)
    llm = StubLlm({"questions": [
        {"gap_id": first.gap_id, "action": "ask", "question": "어느 화면을 바꾸나요?", "choices": []},
        {"gap_id": second.gap_id, "action": "ask", "question": "변경 대상 화면이 무엇인가요?", "choices": [],
         "same_as": first.gap_id}]})
    coord = run(Clarifier(llm, search=SEARCH), [first, second])
    assert len(coord.questions) == 1
    assert set(coord.questions[0].gap_ids) == {first.gap_id, second.gap_id}


def test_question_supersedes_assumption_about_same_information():
    earlier = gap(stage="agent1", slot="화면_변경_내용", missing="어떤 화면을 바꾸나요?", blocking=False)
    coord = run(Clarifier(StubLlm(), search=SEARCH), [earlier])
    assert len(coord.assumptions) == 1
    later = gap(stage="agent2", slot="target_screen_and_expected_change", missing="변경 대상 화면이 무엇인가요?", blocking=True)
    llm = StubLlm({"questions": [{"gap_id": later.gap_id, "action": "ask", "question": "변경 대상 화면이 무엇인가요?",
                                  "choices": [], "supersedes": [earlier.gap_id, "gap-unknown"]}]})
    coord = run(Clarifier(llm, search=SEARCH), [earlier, later], coord)
    assert coord.assumptions == []
    assert earlier.gap_id in coord.questions[0].gap_ids and "gap-unknown" not in coord.questions[0].gap_ids
    assert coord.questions[0].text == "변경 대상 화면이 무엇인가요?"  # 질문 문장·근거는 대체한 gap이 아닌 원래 gap 기준


def test_gap_becoming_blocking_retires_its_earlier_assumption_without_model_help():
    non_blocking = gap(blocking=False)
    coord = run(Clarifier(StubLlm(), search=SEARCH), [non_blocking])
    now_blocking = gap(blocking=True)  # 같은 항목·slot — 다시 실행된 에이전트가 필수로 올림
    coord = run(Clarifier(StubLlm(error=RuntimeError()), search=SEARCH), [now_blocking], coord)
    assert coord.assumptions == [] and len(coord.questions) == 1


# ---- F단계: 진행 자료(Jira)를 선택지·조회 해결에 쓴다 ----
def _activity():
    from app.integrations.work_activity.base import WorkActivityTools
    from app.integrations.work_activity.fake import FakeWorkActivitySource
    from app.orchestration.clarifier import ActivityCandidates
    return ActivityCandidates(WorkActivityTools(FakeWorkActivitySource(), clock=lambda: "2026-11-27T10:00:00+00:00"))


def test_only_agent3_information_can_be_filled_from_activity():
    q = lambda stage: Question(question_id=f"{stage}-q", case_id="C", stage=stage, item_ids=["R1"], field="reuse",
                               text="기존 카드를 재사용할 수 있나요?", reason="r", target="pm", blocking=True)
    resolvers = [gaps_from_output(stage, SimpleNamespace(gaps=[], questions=[q(stage)]))[0].resolvable_by
                 for stage in ("agent1", "agent2", "agent3")]
    assert [("activity" in r, "docs" in r) for r in resolvers] == [(False, False), (False, False), (True, True)]


def test_activity_candidates_back_choices_with_progress_evidence():
    g = gap(stage="agent1", slot="target_screen", missing="어느 추천 화면을 바꾸나요?", blocking=True)
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": "어느 추천 화면을 바꾸나요?",
        "choices": [{"label": "관광지 추천 카드", "evidence_refs": ["TRIP-101"]},
                    {"label": "추천 고도화 에픽 전체", "evidence_refs": ["TRIP-100"]}]}]})
    coord = run(Clarifier(llm, search=SEARCH, activity=_activity()), [g])
    sent = llm.calls[0]["user"]
    assert '"source": "activity"' in sent and '"source": "contract"' in sent
    assert sent.count('"ref": "TRIP-100"') == 1  # 검색에도 걸린 에픽은 후보에 한 번만
    q = coord.questions[0]
    assert q.choices == ["관광지 추천 카드", "추천 고도화 에픽 전체"]
    ev = q.choice_evidence["관광지 추천 카드"][0]
    assert (ev.source_type, ev.system, ev.ref, ev.title) == ("activity", "jira", "TRIP-101", "관광지 추천 답변 카드 UI")


@pytest.mark.parametrize("stage, resolvable, asked", [("agent3", ["pm", "activity"], False), ("agent1", ["pm"], True)])
def test_activity_resolves_only_progress_information(stage, resolvable, asked):
    g = gap(stage=stage, slot="existing_card", missing="추천 카드가 이미 구현돼 있나요?", blocking=True, resolvable_by=resolvable)
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "resolved", "question": "추천 카드가 이미 구현돼 있나요?",
                                  "resolved_answer": "추천 카드 UI는 TRIP-101로 완료되어 재사용할 수 있습니다.",
                                  "resolved_refs": ["TRIP-101"]}]})
    coord = run(Clarifier(llm, search=SEARCH, activity=_activity()), [g])
    assert bool(coord.questions) is asked
    if not asked:
        a = coord.assumptions[0]
        assert (a.kind, a.evidence[0].source_type, a.evidence[0].ref) == ("resolved", "activity", "TRIP-101")


def test_activity_lookup_failure_still_asks_with_contract_candidates():
    from app.integrations.work_activity.base import WorkActivityError, WorkActivityTools
    from app.integrations.work_activity.fake import FakeWorkActivitySource
    from app.orchestration.clarifier import ActivityCandidates

    class Down(FakeWorkActivitySource):
        def list_project_components(self):
            raise WorkActivityError("unavailable", "Jira 인증 실패")

    g = gap(blocking=True)
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": "검수일은 확정 일정인가요?", "choices": []}]})
    coord = run(Clarifier(llm, search=SEARCH, activity=ActivityCandidates(WorkActivityTools(Down()))), [g])
    assert coord.questions and '"source": "activity"' not in llm.calls[0]["user"]


def test_supervisor_builds_activity_candidates_only_when_configured(monkeypatch):
    from app.core.config import settings
    from app.orchestration.clarifier import ActivityCandidates
    from app.orchestration.run_supervisor import RunSupervisorRuntime
    assert RunSupervisorRuntime._clarifier_activity() is None  # 테스트 기본값 none
    monkeypatch.setattr(settings, "work_activity_source", "fake")
    built = RunSupervisorRuntime._clarifier_activity()
    assert isinstance(built, ActivityCandidates) and built.tools.budget == 6
    monkeypatch.setattr(settings, "work_activity_source", "jira")
    monkeypatch.delenv("PROTOTYPE_LAB", raising=False)
    assert RunSupervisorRuntime._clarifier_activity() is None  # lab 밖에서는 Jira 직접 조회를 쓰지 않는다


# ---- 진행 자료 검색어 ----
@pytest.mark.parametrize("text, expected", [
    ("변경하려는 관광지 추천 조건은 무엇인가요? 관광지 추천 조건 — 운영 방침에 맞게 변경", ["관광지", "추천", "조건", "운영"]),
    ("휠체어 이용자도 갈 수 있는 무장애 관광지만 골라서 보여주는 옵션", ["휠체어", "이용자", "무장애", "관광지"]),
    ("어떤 화면을 변경하면 될까요?", []),
])
def test_search_keywords_drop_particles_and_weak_words(text, expected):
    from app.integrations.work_activity.base import search_keywords
    assert search_keywords(text) == expected


def test_activity_search_narrows_then_widens():
    from app.orchestration.clarifier import ActivityCandidates
    assert ActivityCandidates.queries("주변 맛집 정보를 맛집 카드에 추가할까요?") == ["맛집 주변", "맛집"]
    assert ActivityCandidates.queries("무엇인가요?") == []

    calls = []
    activity = _activity()
    original = activity.tools.search_jira_issues
    # 실제 Jira는 여러 단어를 모두 포함해야 걸린다 — 두 단어 검색이 비면 한 단어로 넓히는지 본다
    activity.tools.search_jira_issues = lambda q, **kw: calls.append(q) or ([] if " " in q else original(q, **kw))
    found = [c["ref"] for c in activity.for_query("관광지 맛집 연동은 어떻게 할까요?")]
    assert calls == ["관광지 맛집", "관광지"]
    assert "TRIP-101" in found



# ---- 총괄 LLM이 검색어를 정한다 ----
def test_model_chosen_queries_drive_document_and_jira_search():
    g = gap(stage="agent1", slot="target_condition", missing="변경하려는 관광지 추천 조건은 무엇인가요?", blocking=True)
    doc_queries, jira_queries = [], []
    activity = _activity()
    original = activity.tools.search_jira_issues
    activity.tools.search_jira_issues = lambda q, **kw: jira_queries.append(q) or original(q, **kw)
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": g.missing, "choices": []}]},
                  plan={"searches": [{"gap_id": g.gap_id, "doc_query": "추천 필터 조건", "jira_query": "맛집"}]})
    clarifier = Clarifier(llm, search=lambda q: doc_queries.append(q) or SEARCH(q), activity=activity)
    run(clarifier, [g])
    assert doc_queries == ["추천 필터 조건"] and jira_queries[0] == "맛집"
    plan_payload = json.loads(llm.plan_calls[0]["user"])
    assert plan_payload["activity_available"] is True and plan_payload["gaps"][0]["gap_id"] == g.gap_id
    assert '"ref": "TRIP-118"' in llm.calls[0]["user"]  # 모델 검색어로 찾은 이슈가 질문 작성에 후보로 간다


def test_search_plan_failure_falls_back_to_rule_keywords():
    g = gap(stage="agent1", slot="target_condition", missing="변경하려는 관광지 추천 조건은 무엇인가요?", blocking=True)
    doc_queries, jira_queries = [], []
    activity = _activity()
    activity.tools.search_jira_issues = lambda q, **kw: jira_queries.append(q) or []
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": g.missing, "choices": []}]})  # plan 없음
    run(Clarifier(llm, search=lambda q: doc_queries.append(q) or [], activity=activity), [g])
    assert len(llm.plan_calls) == 1
    assert doc_queries[0].startswith("변경하려는 관광지 추천 조건은")  # 문서는 gap 문장으로
    assert jira_queries[:2] == ["관광지 추천", "관광지"]          # Jira는 규칙 기반 핵심 단어로


def test_no_search_plan_call_when_nothing_to_ask():
    llm = StubLlm()
    run(Clarifier(llm, search=SEARCH), [gap(blocking=False)])
    assert not llm.plan_calls and not llm.calls


def test_orchestration_prompts_carry_no_test_project_knowledge():
    """새 Jira·문서를 연결해도 같은 지침이어야 한다 — 테스트 프로젝트 분야 단어를 지침에 넣지 않는다."""
    from pathlib import Path
    from app.agents.agent3.activity_probe import PROBE_SYSTEM
    from app.orchestration.clarifier import SEARCH_SYSTEM, WRITER_SYSTEM
    v4 = (Path(__file__).resolve().parents[1] / "app/agents/agent1/prompts/a1-draft-v4.txt").read_text(encoding="utf-8")
    v3 = (Path(__file__).resolve().parents[1] / "app/agents/agent1/prompts/a1-draft-v3.txt").read_text(encoding="utf-8")
    added_in_v4 = "\n".join(line for line in v4.splitlines() if line not in v3.splitlines())
    for prompt in (SEARCH_SYSTEM, WRITER_SYSTEM, PROBE_SYSTEM, added_in_v4):
        for word in ("관광", "맛집", "무장애", "휠체어", "챗봇", "휴무", "TripBranch", "TRIP-", "TP-"):
            assert word not in prompt, word


# ---- 총괄 LLM이 조회할 곳을 고른다 ----
@pytest.mark.parametrize("sources, docs_called, jira_called", [
    (["docs"], True, False),
    (["jira"], False, True),
    ([], False, False),          # 기록이 없을 정보 — 조회 없이 직접 입력으로 묻는다
    (None, True, True),          # 모델이 고르지 않으면 둘 다 조회한다
])
def test_model_chosen_sources_limit_lookups(sources, docs_called, jira_called):
    g = gap(stage="agent1", slot="target_condition", missing="변경하려는 관광지 추천 조건은 무엇인가요?", blocking=True)
    doc_queries, jira_queries = [], []
    activity = _activity()
    original = activity.tools.search_jira_issues
    activity.tools.search_jira_issues = lambda q, **kw: jira_queries.append(q) or original(q, **kw)
    plan = {"gap_id": g.gap_id, "doc_query": "추천 조건", "jira_query": "맛집"}
    if sources is not None:
        plan["sources"] = sources
    llm = StubLlm({"questions": [{"gap_id": g.gap_id, "action": "ask", "question": g.missing, "choices": []}]},
                  plan={"searches": [plan]})
    coord = run(Clarifier(llm, search=lambda q: doc_queries.append(q) or SEARCH(q), activity=activity), [g])
    sent = llm.calls[0]["user"]
    assert bool(doc_queries) is docs_called and ('"source": "contract"' in sent) is docs_called
    assert bool(jira_queries) is jira_called and ('"source": "activity"' in sent) is jira_called
    assert coord.questions  # 조회 대상과 무관하게 질문은 만든다


def test_search_prompt_explains_source_choice():
    from app.orchestration.clarifier import SEARCH_SYSTEM
    assert "sources" in SEARCH_SYSTEM and '"docs"' in SEARCH_SYSTEM and '"jira"' in SEARCH_SYSTEM
