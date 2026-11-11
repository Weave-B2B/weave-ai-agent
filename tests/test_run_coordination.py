"""Strict local contract checks, not evidence of a live Backend integration."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from app.agents.fixture import load_chain
from app.orchestration.coordinated_execution import CoordinatedAgentRunner
from app.orchestration.fake_run_coordination import FakeCoordinatedRunStateClient
from app.orchestration.http_run_coordination import HttpCoordinatedRunStateClient
from app.orchestration.http_run_state import RunRequestRejected
from app.orchestration.payload import to_commit_payload
from app.orchestration.run_agent3_payload import Agent3RunOutput
from app.orchestration.run_coordination import (
    AcquireExecution, AnswerQuestion, CoordinationState, FinalReviewDecision, FinishUnchanged,
    FencedCommit, PMQuestion, RequestFinalReview, ReviewBundle, SaveCoordination, payload_fingerprint,
)
from app.orchestration.run_state import InvalidStateTransition, StateRevisionConflict
from schema.schemas import Agent1Output


class Flow:
    def __init__(self):
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        self.client = FakeCoordinatedRunStateClient(clock=lambda: self.now)
        self.client.create_run("run")
        self.seq = 0

    def request(self, cls, **kwargs):
        self.seq += 1
        return cls(run_id="run", expected_state_revision=self.client.get_state("run").state_revision,
                   operation_id=f"op-{self.seq}", **kwargs)

    def acquire(self, agent, **kwargs):
        return self.client.acquire_execution(self.request(AcquireExecution, agent_type=agent, **kwargs))

    def complete(self, agent, rid, payload):
        started = self.acquire(agent)
        state = self.client.get_coordinated_run("run")
        return self.client.commit_result(self.request(
            FencedCommit, agent_type=agent, execution_token=started.execution_token,
            upstream_result_ids=state.executions[agent].upstream_result_ids,
            result_id=rid, result_payload=payload))


@pytest.fixture
def flow():
    return Flow()


@pytest.fixture
def ready(flow):
    flow.complete("agent1", "a1", {"items": ["R1"]})
    flow.complete("agent2", "a2", {"assessments": ["R1"]})
    original = load_chain()["CR-A-001"]["agent3_gold"]
    out = Agent3RunOutput.model_validate({**original, "documents": {}})
    out.meta.result_id = "impact-1"
    out.documents = {}
    raw = out.model_dump(mode="json")
    raw["documents"] = {"email": {"result_id": "doc-1", "impact_result_id": "impact-1",
        "a2_result_id": "a2", "model": "fake", "subject": "변경 검토", "body": "고객 협의 전 초안입니다."}}
    out = Agent3RunOutput.model_validate(raw)
    flow.complete("agent3", "a3", to_commit_payload(out))
    return flow


def question(qid="q1", field="photo_reuse"):
    return PMQuestion(question_id=qid, item_ids=["R1"], field=field,
                      text="기존 기능을 재사용할 수 있나요?", choices=["예", "아니요", "모름"])


def bundle(flow):
    payload = flow.client.get_result("run", "a3").result_payload
    return ReviewBundle(result_ids={"agent1": "a1", "agent2": "a2", "agent3": "a3"},
                        document_fingerprint=payload_fingerprint(payload["documents"]))


def test_unchanged_closes_running_without_changing_result_or_downstream(ready):
    f = ready
    token = f.acquire("agent2").execution_token
    req = f.request(FinishUnchanged, agent_type="agent2", execution_token=token,
                    target_result_id="a2", observed_payload={"assessments": ["R1"], "meta": {"model": "new"}})
    receipt = f.client.finish_unchanged(req)
    state = f.client.get_state("run")
    assert state.agents["agent2"].status == "COMPLETED"
    assert (receipt.result_id, receipt.result_revision) == ("a2", 1)
    assert state.agents["agent3"].upstream_result_ids == ["a1", "a2"]
    assert not state.agents["agent3"].stale
    assert f.client.finish_unchanged(req).replayed


def test_changed_output_cannot_be_marked_unchanged(ready):
    f = ready
    token = f.acquire("agent2").execution_token
    with pytest.raises(InvalidStateTransition):
        f.client.finish_unchanged(f.request(FinishUnchanged, agent_type="agent2", execution_token=token,
            target_result_id="a2", observed_payload={"assessments": ["different"]}))
    assert f.client.get_state("run").agents["agent2"].status == "RUNNING"


def test_recovered_execution_rejects_late_worker_even_with_latest_revision(flow):
    old = flow.acquire("agent1")
    flow.now += timedelta(seconds=601)
    new = flow.acquire("agent1", recover_expired=True)
    assert new.execution_token != old.execution_token
    with pytest.raises(InvalidStateTransition):
        flow.client.commit_result(flow.request(FencedCommit, agent_type="agent1", result_id="old",
            execution_token=old.execution_token, result_payload={"items": []}))
    req = flow.request(FencedCommit, agent_type="agent1", result_id="new",
                       execution_token=new.execution_token, result_payload={"items": ["R1"]})
    first = flow.client.commit_result(req)
    assert flow.client.commit_result(req).replayed
    assert first.result_revision == 1


def test_active_execution_cannot_be_stolen(flow):
    flow.acquire("agent1")
    with pytest.raises(InvalidStateTransition):
        flow.acquire("agent1", recover_expired=True)


def test_upstream_changed_during_execution_cannot_commit(ready):
    f = ready
    token = f.acquire("agent3").execution_token
    f.complete("agent2", "a2-new", {"assessments": ["changed"]})
    with pytest.raises(InvalidStateTransition):
        f.client.commit_result(f.request(FencedCommit, agent_type="agent3", result_id="a3-late",
            execution_token=token, upstream_result_ids=["a1", "a2"], result_payload={}))


def test_acquire_is_idempotent(flow):
    req = flow.request(AcquireExecution, agent_type="agent1")
    first = flow.client.acquire_execution(req)
    again = flow.client.acquire_execution(req)
    assert again.replayed and again.execution_token == first.execution_token


def test_answers_atomically_resume_only_after_all_blockers_resolved(ready):
    f = ready
    state = CoordinationState(questions=[question(), question("q2", "photo_limit")], llm_calls=5)
    f.client.save_coordination(f.request(SaveCoordination, state=state))
    first = f.request(AnswerQuestion, question_id="q1", answer="예", answered_by="pm")
    assert f.client.answer_question(first).status == "WAITING_INPUT"
    assert f.client.answer_question(first).replayed
    with pytest.raises(InvalidStateTransition):
        f.acquire("agent3")
    last = f.request(AnswerQuestion, question_id="q2", answer="최대 3장", answered_by="pm")
    assert f.client.answer_question(last).status == "RUNNING"
    state = f.client.get_coordinated_run("run")
    assert state.coordination.llm_calls == 5
    assert all(q.status == "resolved" for q in state.coordination.questions)
    assert state.state.agents["agent2"].stale and state.state.agents["agent3"].stale
    assert state.review_bundle is None


def test_unknown_answer_keeps_blocker_and_history(ready):
    f = ready
    f.client.save_coordination(f.request(SaveCoordination, state=CoordinationState(questions=[question()])))
    f.client.answer_question(f.request(AnswerQuestion, question_id="q1", answer="모름", answered_by="pm"))
    state = f.client.get_coordinated_run("run")
    assert state.state.status == "WAITING_INPUT"
    assert state.coordination.questions[0].status == "open"
    assert f.client.history("run")[-1]["resolved"] is False


def test_stale_answer_revision_does_not_change_question(ready):
    f = ready
    f.client.save_coordination(f.request(SaveCoordination, state=CoordinationState(questions=[question()])))
    req = f.request(AnswerQuestion, question_id="q1", answer="예", answered_by="pm")
    req.expected_state_revision -= 1
    with pytest.raises(StateRevisionConflict):
        f.client.answer_question(req)
    assert f.client.get_coordinated_run("run").coordination.questions[0].answer is None


def test_cannot_reset_budget_or_drop_question(ready):
    f = ready
    state = CoordinationState(questions=[question()], llm_calls=7, segment_steps=2)
    f.client.save_coordination(f.request(SaveCoordination, state=state))
    for new in (state.model_copy(update={"llm_calls": 0}), state.model_copy(update={"questions": []})):
        with pytest.raises(InvalidStateTransition):
            f.client.save_coordination(f.request(SaveCoordination, state=new))


def test_failed_state_update_is_atomic(flow):
    flow.acquire("agent1")
    before = flow.client.get_coordinated_run("run")
    with pytest.raises(InvalidStateTransition):
        flow.client.save_coordination(flow.request(SaveCoordination, state=CoordinationState(questions=[question()])))
    assert flow.client.get_coordinated_run("run") == before


def test_snapshot_and_payload_are_detached(ready):
    f = ready
    fetched = f.client.get_result("run", "a3")
    fetched.result_payload["documents"]["email"]["body"] = "changed outside transaction"
    assert f.client.get_result("run", "a3").result_payload["documents"]["email"]["body"] != "changed outside transaction"


def test_only_exact_bundle_final_approval_completes_run(ready):
    f = ready
    target = bundle(f)
    assert f.client.get_state("run").status == "RUNNING"
    f.client.request_final_review(f.request(RequestFinalReview, bundle=target))
    wrong = target.model_copy(update={"document_fingerprint": "0" * 64})
    with pytest.raises(InvalidStateTransition):
        f.client.final_review(f.request(FinalReviewDecision, bundle=wrong, reviewed_by="pm", decision="approve"))
    assert f.client.final_review(f.request(FinalReviewDecision, bundle=target, reviewed_by="pm", decision="approve")).status == "COMPLETED"


def test_new_result_cannot_receive_old_bundle_approval(ready):
    f = ready
    target = bundle(f)
    payload = f.client.get_result("run", "a3").result_payload
    payload["documents"]["email"]["body"] = "revised draft"
    f.complete("agent3", "a3-new", payload)
    with pytest.raises(InvalidStateTransition):
        f.client.request_final_review(f.request(RequestFinalReview, bundle=target))


def test_automatic_commit_cannot_mark_run_completed(flow):
    lease = flow.acquire("agent1")
    with pytest.raises(InvalidStateTransition):
        flow.client.commit_result(flow.request(FencedCommit, agent_type="agent1", execution_token=lease.execution_token,
            result_id="a1", result_payload={}, requested_run_status="COMPLETED"))


def test_questions_same_item_different_fields_are_not_collapsed():
    state = CoordinationState(questions=[question(), question("q2", "photo_limit")])
    assert len(state.questions) == 2
    with pytest.raises(ValidationError):
        CoordinationState(questions=[question(), question("q2")])


def test_answers_cannot_smuggle_trusted_metadata():
    with pytest.raises(ValidationError):
        AnswerQuestion(run_id="run", expected_state_revision=0, operation_id="op", question_id="q",
                       answer="예", answered_by="pm", llm_calls=0, quote_links={})


def test_agent3_payload_preserves_shared_work_needs_and_documents(ready):
    raw = ready.client.get_result("run", "a3").result_payload
    raw["estimates"][0]["lines"][0]["shared_work_key"] = "common-upload"
    raw["status"] = "NEEDS_INPUT"
    raw["needs"] = [{"kind": "more_evidence", "item_ids": ["R1"], "topic": "재사용 조건"}]
    persisted = to_commit_payload(Agent3RunOutput.model_validate(raw))
    restored = Agent3RunOutput.model_validate(persisted)
    assert restored.needs[0].target == "agent2"
    assert restored.estimates[0].lines[0].shared_work_key == "common-upload"
    assert restored.documents["email"].body
    assert persisted["meta"]["debug"] == {}


def test_proposed_http_contract_disabled_without_explicit_opt_in():
    calls = []
    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(lambda req: calls.append(req)))
    client = HttpCoordinatedRunStateClient("http://backend", "token", "member", http=http)
    with pytest.raises(RunRequestRejected):
        client.get_coordinated_run("run")
    assert not calls


def test_opt_in_http_acquire_uses_dedicated_endpoint():
    def handle(req):
        assert req.url.path == "/internal/v1/runs/run/executions/acquire"
        assert req.headers["X-Actor-Member-Id"] == "member"
        return httpx.Response(200, json={"run_id": "run", "operation_id": "op", "state_revision": 1,
                                         "status": "RUNNING", "execution_token": "lease"})
    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(handle))
    client = HttpCoordinatedRunStateClient("http://backend", "token", "member", http=http, coordination_enabled=True)
    result = client.acquire_execution(AcquireExecution(run_id="run", agent_type="agent1",
                                                      expected_state_revision=0, operation_id="op"))
    assert result.execution_token == "lease"


def test_runner_reuses_unchanged_result_and_retries_only_storage(flow):
    calls = []
    output = Agent1Output.model_validate(load_chain()["CR-A-001"]["agent1_gold"])
    def worker():
        calls.append("model")
        return output.model_copy(deep=True)
    runner = CoordinatedAgentRunner(flow.client)
    flow.client.fail_next_write(only=FencedCommit)
    first = runner.execute("run", "agent1", worker)
    second = runner.execute("run", "agent1", worker)
    assert calls == ["model", "model"]  # no model replay on storage retry
    assert first.result_id == second.result_id
    assert second.result_revision == 1
    assert flow.client.get_state("run").agents["agent1"].status == "COMPLETED"


def test_failed_worker_is_not_left_running(flow):
    runner = CoordinatedAgentRunner(flow.client)
    def broken_worker():
        raise RuntimeError("private provider detail")
    with pytest.raises(RuntimeError):
        runner.execute("run", "agent1", broken_worker)
    assert flow.client.get_state("run").agents["agent1"].status == "FAILED"
    assert flow.client.get_coordinated_run("run").executions == {}
    result = next(x["result_id"] for x in flow.client.history("run") if x["event"] == "result_committed")
    assert "private provider detail" not in str(flow.client.get_result("run", result).result_payload)


def test_final_review_rejects_document_from_old_scope_result(ready):
    f = ready
    payload = f.client.get_result("run", "a3").result_payload
    payload["documents"]["email"]["a2_result_id"] = "a2-old"
    f.complete("agent3", "a3-new", payload)
    target = ReviewBundle(result_ids={"agent1": "a1", "agent2": "a2", "agent3": "a3-new"},
                          document_fingerprint=payload_fingerprint(payload["documents"]))
    with pytest.raises(InvalidStateTransition):
        f.client.request_final_review(f.request(RequestFinalReview, bundle=target))


def test_additional_questions_preserve_prior_answer(ready):
    f = ready
    f.client.save_coordination(f.request(SaveCoordination, state=CoordinationState(questions=[question()])))
    f.client.answer_question(f.request(AnswerQuestion, question_id="q1", answer="예", answered_by="pm"))
    state = f.client.get_coordinated_run("run").coordination
    state.questions.append(question("q2", "photo_limit"))
    f.client.save_coordination(f.request(SaveCoordination, state=state))
    saved = f.client.get_coordinated_run("run").coordination
    assert saved.questions[0].answer == "예" and saved.questions[0].status == "resolved"
    assert saved.questions[1].status == "open"


def test_question_limit_and_budget_bounds():
    with pytest.raises(ValidationError):
        CoordinationState(llm_calls=81)
    with pytest.raises(ValidationError):
        CoordinationState(segment_llm_calls=41)
    with pytest.raises(ValidationError):
        CoordinationState(segment_steps=13)


def test_more_than_four_questions_rejected_without_changing_state(flow):
    before = flow.client.get_coordinated_run("run")
    questions = [question(f"q{n}", f"field{n}") for n in range(5)]
    with pytest.raises(InvalidStateTransition):
        flow.client.save_coordination(flow.request(SaveCoordination, state=CoordinationState(questions=questions)))
    assert flow.client.get_coordinated_run("run") == before


def test_new_pm_blocker_reopens_final_review_instead_of_stuck_waiting(ready):
    f = ready
    f.client.request_final_review(f.request(RequestFinalReview, bundle=bundle(f)))
    f.client.save_coordination(f.request(SaveCoordination, state=CoordinationState(questions=[question()])))
    state = f.client.get_coordinated_run("run")
    assert state.state.status == "WAITING_INPUT"
    assert state.state.pending_review is None and state.review_bundle is None


def test_missing_documents_prevent_final_review(ready):
    f = ready
    payload = f.client.get_result("run", "a3").result_payload
    payload["documents"] = {}
    f.complete("agent3", "a3-without-docs", payload)
    target = ReviewBundle(result_ids={"agent1": "a1", "agent2": "a2", "agent3": "a3-without-docs"},
                          document_fingerprint=payload_fingerprint({}))
    with pytest.raises(InvalidStateTransition):
        f.client.request_final_review(f.request(RequestFinalReview, bundle=target))


def test_mutating_receipt_cannot_change_replay(flow):
    req = flow.request(AcquireExecution, agent_type="agent1")
    first = flow.client.acquire_execution(req)
    first.execution_token = "tampered"
    assert flow.client.acquire_execution(req).execution_token != "tampered"


def test_legacy_review_cannot_bypass_final_bundle(ready):
    from app.orchestration.run_state import ResolveReviewRequest
    f = ready
    f.client.request_final_review(f.request(RequestFinalReview, bundle=bundle(f)))
    with pytest.raises(InvalidStateTransition):
        f.client.resolve_review(f.request(ResolveReviewRequest, resolved_by="pm", decision="APPROVED",
                                         requested_run_status="COMPLETED"))
