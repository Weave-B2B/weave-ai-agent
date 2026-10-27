"""오케스트레이션: 멈춤·재개·재실행·무효화. fixture 에이전트로 데이터셋 30건을 끝까지 돌린다."""
import json

import pytest
from conftest import EVAL, jl
from schema.schemas import Agent1Input, Agent1Output, Agent2Output, AgentError, Answer, PMReview
from app.agents.base import AgentFailure, AgentSet
from app.agents.fixture import FixtureAgent1, FixtureAgent2, FixtureAgent3, load_chain
from app.orchestration.engine import Engine, InvalidTransition, VersionConflict
from app.orchestration.state_store import InMemoryStore

RC = "RC-SYNTH-2026H2-v1"


class Rec3(FixtureAgent3):
    def __init__(self, chain):
        super().__init__(chain); self.calls = []

    def run(self, inp):
        self.calls.append(inp); return super().run(inp)


def make(chain=None, a1=None, a2=None):
    chain = chain or load_chain()
    a3 = Rec3(chain)
    return Engine(AgentSet(a1 or FixtureAgent1(chain), a2 or FixtureAgent2(chain), a3), InMemoryStore()), a3


def bound_review(row, rec) -> PMReview:
    """가상 PM 검토를 실제 ② result_id에 묶는다 (README: 테스트 어댑터)."""
    return PMReview.model_validate({**row["pm_review"], "target_result_id": rec.a2.meta.result_id})


def drive(eng, row):
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    assert (rec.state.status, rec.state.stage) == ("WAITING_PM_REVIEW", "pm_items")
    rec = eng.confirm_items(row["case_id"], rec.a1.items, rec.state.state_revision)
    assert (rec.state.status, rec.state.stage) == ("WAITING_PM_REVIEW", "pm_review")
    rec = eng.submit_pm_review(row["case_id"], bound_review(row, rec), rec.state.state_revision)
    if rec.state.stage == "lead_review":
        rec = eng.lead_decision(row["case_id"], True, rec.state.state_revision)
    return rec


def test_all_30_cases_reach_expected_status(chain):
    for row in chain:
        eng, _ = make()
        rec = drive(eng, row)
        assert rec.state.status == row["expected_workflow_status"], row["case_id"]
        assert bool(rec.state.open_question_ids) == row["has_unresolved_followup"], row["case_id"]
        assert set(rec.state.open_question_ids) == set(row["open_question_ids"]), row["case_id"]


def test_a3_input_built_by_engine_matches_dataset(chain):
    checked = 0
    for row in chain:
        eng, a3 = make()
        drive(eng, row)
        if not a3.calls:
            continue
        checked += 1
        got, want = a3.calls[0].model_dump(), row["agent3_input"]
        for k in ("context", "rate_card_id", "received_at", "items", "constraints", "answers"):
            assert got[k] == want[k], (row["case_id"], k)
    assert checked >= 10


def test_a3_not_called_when_no_target(chain):
    row = next(r for r in chain if r["agent3_gold"]["status"] == "NOT_REQUIRED")
    eng, a3 = make()
    rec = drive(eng, row)
    assert a3.calls == [] and rec.a3.status == "NOT_REQUIRED" and rec.a3.estimates == []
    assert rec.a3.review_required is True and rec.a3.billing_authorized is False


def test_a3_amounts_come_from_calc_cost_with_trace(chain):
    from app.tools.trace import TOOL_LOG
    row = next(r for r in chain if r["agent3_gold"]["status"] == "IMPACT_DRAFT_READY")
    TOOL_LOG.clear()
    eng, _ = make()
    rec = drive(eng, row)
    drafts = [e for e in rec.a3.estimates if e.status == "draft"]
    assert drafts and all(e.totals_source == "calc_cost" and e.totals for e in drafts)
    calls = [t for t in TOOL_LOG if t["tool"] == "calc_cost" and t["result_id"] == rec.a3.meta.result_id]
    assert len(calls) == len(drafts) and all(t["ok"] for t in calls)     # 문자열이 아니라 실제 호출 기록


def test_results_are_stamped_by_caller_not_agent(chain):
    eng, _ = make()
    row = chain[0]
    rec = drive(eng, row)
    assert rec.a1.meta.result_id.startswith("agent1-") and rec.a1.meta.result_id != row["agent1_gold"]["meta"]["result_id"]
    assert rec.a2.meta.upstream_result_ids == [rec.a1.meta.result_id]
    assert rec.a3.meta.upstream_result_ids == [rec.a2.meta.result_id, rec.pm_review.review_id]


def test_stale_state_revision_is_rejected(chain):
    eng, _ = make()
    row = chain[0]
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    old = rec.state.state_revision
    eng.confirm_items(row["case_id"], rec.a1.items, old)
    with pytest.raises(VersionConflict):
        eng.confirm_items(row["case_id"], rec.a1.items, old)         # 오래된 화면의 덮어쓰기 차단


def test_pm_review_for_old_a2_result_is_version_conflict(chain):
    eng, _ = make()
    row = chain[0]
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    rec = eng.confirm_items(row["case_id"], rec.a1.items, rec.state.state_revision)
    stale = PMReview.model_validate({**row["pm_review"], "target_result_id": "A2-OLD"})
    with pytest.raises(VersionConflict):
        eng.submit_pm_review(row["case_id"], stale, rec.state.state_revision)


def test_pending_pm_review_keeps_waiting(chain):
    eng, a3 = make()
    row = chain[0]
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    rec = eng.confirm_items(row["case_id"], rec.a1.items, rec.state.state_revision)
    pending = bound_review(row, rec).model_copy(update={"status": "PENDING"})
    rec = eng.submit_pm_review(row["case_id"], pending, rec.state.state_revision)
    assert rec.state.stage == "pm_review" and rec.state.status == "WAITING_PM_REVIEW" and a3.calls == []


def test_wrong_stage_input_is_rejected(chain):
    eng, _ = make()
    row = chain[0]
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    with pytest.raises(InvalidTransition):
        eng.lead_decision(row["case_id"], True, rec.state.state_revision)


def test_lead_rejection_reruns_a3_only_and_keeps_old_result(chain):
    row = next(r for r in chain if r["agent3_gold"]["status"] == "IMPACT_DRAFT_READY")
    eng, a3 = make()
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    rec = eng.confirm_items(row["case_id"], rec.a1.items, rec.state.state_revision)
    rec = eng.submit_pm_review(row["case_id"], bound_review(row, rec), rec.state.state_revision)
    a2_id, first_a3 = rec.a2.meta.result_id, rec.a3.meta.result_id
    rec = eng.lead_decision(row["case_id"], False, rec.state.state_revision, "공수 재검토")
    assert rec.a2.meta.result_id == a2_id                      # ②는 그대로
    assert rec.a3.meta.result_id != first_a3 and first_a3 in rec.state.stale_result_ids and first_a3 in rec.archive
    assert rec.a3.meta.result_revision == 2 and rec.state.stage == "lead_review" and len(a3.calls) == 2


class AnswerAnalysis(FixtureAgent1):
    def __init__(self, chain, rerun_from, resolved=True):
        super().__init__(chain); self.rerun_from, self.resolved = rerun_from, resolved

    def analyze_answer(self, inp):
        from schema.schemas import AnswerAnalysisOutput
        return AnswerAnalysisOutput(context=inp.context, question_id=inp.question.question_id, resolved=self.resolved, rerun_from=self.rerun_from)


def _answered_case(chain, rerun_from):
    row = next(r for r in chain if r["open_question_ids"])
    eng, _ = make(chain={r["case_id"]: r for r in chain}, a1=AnswerAnalysis({r["case_id"]: r for r in chain}, rerun_from))
    rec = drive(eng, row)
    qid = row["open_question_ids"][0]
    ans = Answer(answer_id="ANS-1", question_id=qid, text="확인했습니다", answered_by="pm", answered_at="2026-10-27T10:00:00+09:00")
    return eng, row, rec, qid, ans


def test_answer_rerun_from_agent2_invalidates_a2_review_a3(chain):
    eng, row, rec, qid, ans = _answered_case(chain, "agent2")
    old = {"a1": rec.a1.meta.result_id, "a2": rec.a2.meta.result_id, "pmr": rec.pm_review.review_id}
    rec = eng.answer_question(row["case_id"], qid, ans, rec.state.state_revision)
    assert rec.a1.meta.result_id == old["a1"]                  # ①은 유지
    assert rec.a2.meta.result_id != old["a2"] and old["a2"] in rec.state.stale_result_ids
    assert old["pmr"] in rec.state.stale_result_ids and rec.pm_review is None
    assert (rec.state.status, rec.state.stage) == ("WAITING_PM_REVIEW", "pm_review")
    assert rec.questions[qid].status == "resolved" and rec.inp.context.request_revision == row["agent1_input"]["context"]["request_revision"]


def test_answer_without_rerun_keeps_everything(chain):
    eng, row, rec, qid, ans = _answered_case(chain, None)
    before = rec.a3.meta.result_id if rec.a3 else None
    rec = eng.answer_question(row["case_id"], qid, ans, rec.state.state_revision)
    assert (rec.a3.meta.result_id if rec.a3 else None) == before and rec.state.stale_result_ids == []
    assert qid not in rec.state.open_question_ids


def test_answer_does_not_resolve_when_analysis_says_no(chain):
    eng, _row, rec, qid, ans = None, None, None, None, None
    chain_d = {r["case_id"]: r for r in chain}
    row = next(r for r in chain if r["open_question_ids"])
    eng, _ = make(chain=chain_d, a1=AnswerAnalysis(chain_d, None, resolved=False))
    rec = drive(eng, row)
    qid = row["open_question_ids"][0]
    rec = eng.answer_question(row["case_id"], qid, Answer(answer_id="A", question_id=qid, text="모르겠음", answered_by="pm", answered_at="2026-10-27T10:00:00+09:00"), rec.state.state_revision)
    assert rec.questions[qid].status == "answered"             # 답변 업로드만으로 해소하지 않는다


def test_changed_request_needs_new_revision_and_stales_everything(chain):
    row = chain[0]
    eng, _ = make()
    rec = drive(eng, row)
    old_ids = [rec.a1.meta.result_id, rec.a2.meta.result_id]
    changed = json.loads(json.dumps(row["agent1_input"]))
    changed["message_text"] += " 배너도 추가해 주세요."
    with pytest.raises(VersionConflict):
        eng.start(Agent1Input.model_validate(changed), RC)       # 같은 revision·analysis로는 불가
    changed["context"]["request_revision"] = 2
    changed["context"]["analysis_id"] = row["agent1_input"]["context"]["analysis_id"] + "-r2"
    rec2 = eng.start(Agent1Input.model_validate(changed), RC)
    assert all(i in rec2.state.stale_result_ids for i in old_ids)
    assert rec2.a1.meta.result_id not in old_ids and rec2.state.stage == "pm_items"


def test_pm_edit_is_marked_source_pm(chain):
    eng, _ = make()
    row = chain[0]
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    items = [i.model_copy() for i in rec.a1.items]
    items[0] = items[0].model_copy(update={"text": items[0].text + " (PM 수정)"})
    rec = eng.confirm_items(row["case_id"], items, rec.state.state_revision)
    assert rec.items[0].source == "pm" and rec.items[1].source != "pm"


def test_agent_error_output_fails_without_passing_downstream(chain):
    chain_d = load_chain()

    class Err1(FixtureAgent1):
        def run(self, inp):
            out = super().run(inp).model_dump()
            out.update(status="ERROR", items=[], error=AgentError(code="MODEL_TIMEOUT", message="x", stage="agent1", retryable=True).model_dump())
            return Agent1Output.model_validate(out)

    eng, _ = make(chain_d, a1=Err1(chain_d))
    row = chain[0]
    rec = eng.start(Agent1Input.model_validate(row["agent1_input"]), RC)
    assert rec.state.status == "FAILED" and rec.state.error.code == "MODEL_TIMEOUT" and rec.a1 is None


def test_retryable_failure_is_retried_up_to_two_times_then_ok(chain):
    chain_d = load_chain()

    class Flaky(FixtureAgent1):
        n = 0

        def run(self, inp):
            Flaky.n += 1
            if Flaky.n <= 2:
                raise AgentFailure(AgentError(code="TOOL_TIMEOUT", message="t", stage="agent1", retryable=True))
            return super().run(inp)

    eng, _ = make(chain_d, a1=Flaky(chain_d))
    rec = eng.start(Agent1Input.model_validate(chain[0]["agent1_input"]), RC)
    assert rec.state.status == "WAITING_PM_REVIEW" and Flaky.n == 3

    class Dead(FixtureAgent1):
        def run(self, inp):
            raise AgentFailure(AgentError(code="TOOL_FAILED", message="t", stage="agent1", retryable=False))

    eng, _ = make(chain_d, a1=Dead(chain_d))
    rec = eng.start(Agent1Input.model_validate(chain[0]["agent1_input"]), RC)
    assert rec.state.status == "FAILED" and rec.state.error.code == "TOOL_FAILED"
    eng.agents.agent1 = FixtureAgent1(chain_d)                   # 복구 후 재시도: 새 result_id로 같은 단계부터
    rec = eng.retry(chain[0]["case_id"], rec.state.state_revision)
    assert rec.state.status == "WAITING_PM_REVIEW" and rec.a1 is not None


def test_blocking_question_stops_flow_with_waiting_input():
    f = next(f for f in jl(EVAL / "contract_tests" / "focused.jsonl") if f["fixture_id"] == "A1-AMBIGUOUS")
    chain_d = load_chain()

    class Ambiguous(FixtureAgent1):
        def run(self, inp):
            return Agent1Output.model_validate(f["expected_output"])

    eng, _ = make(chain_d, a1=Ambiguous(chain_d))
    rec = eng.start(Agent1Input.model_validate(f["input"]), RC)
    assert rec.state.status == "WAITING_INPUT" and rec.state.stage == "agent1" and rec.a2 is None
    assert rec.state.open_question_ids


def test_start_rejects_unknown_baseline_and_rate_card(chain):
    from app.orchestration.engine import InvalidInput
    eng, _ = make()
    bad = json.loads(json.dumps(chain[0]["agent1_input"])); bad["context"]["baseline_id"] = "P-COM-101:BL-9"
    with pytest.raises(InvalidInput): eng.start(Agent1Input.model_validate(bad), RC)
    with pytest.raises(InvalidInput): eng.start(Agent1Input.model_validate(chain[0]["agent1_input"]), "RC-NOPE")
