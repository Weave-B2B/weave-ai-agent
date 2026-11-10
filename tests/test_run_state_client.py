import pytest

from app.orchestration.fake_run_state import FakeRunStateClient
from app.orchestration.run_state import (
    CommitResultRequest, InvalidStateTransition, OperationConflict, RecordEditRequest, RequestReviewRequest, ResolveReviewRequest,
    RunNotFound, RunStateClient, RunStateError, StatePersistenceError, StateRevisionConflict,
)


@pytest.fixture
def fake():
    f = FakeRunStateClient()
    f.create_run("run-1")
    return f


def commit(rev=0, op="op-1", agent="agent1", result_id="r1", **kw):
    return CommitResultRequest(run_id="run-1", expected_state_revision=rev, operation_id=op, agent_type=agent,
                               result_id=result_id, result_payload={"k": 1}, **kw)


def test_fake_satisfies_port_and_returns_initial_state(fake):
    client: RunStateClient = fake
    s = client.get_state("run-1")
    assert (s.state_revision, s.status, s.pending_review) == (0, "QUEUED", None)
    assert set(s.agents) == {"agent1", "agent2", "agent3"}
    assert all(a.status == "NOT_STARTED" and a.current_result_id is None for a in s.agents.values())


def test_commit_increments_revision_and_sets_current_result(fake):
    r = fake.commit_result(commit())
    assert (r.state_revision, r.result_revision, r.status, r.replayed) == (1, 1, "RUNNING", False)
    s = fake.get_state("run-1")
    a1 = s.agents["agent1"]
    assert s.state_revision == 1 and (a1.status, a1.current_result_id, a1.result_revision) == ("COMPLETED", "r1", 1)
    assert s.agents["agent2"].status == "NOT_STARTED"
    r2 = fake.commit_result(commit(rev=1, op="op-2", result_id="r2", upstream_result_ids=["r1"]))
    assert r2.result_revision == 2 and fake.get_state("run-1").agents["agent1"].current_result_id == "r2"


def test_same_operation_is_not_committed_twice(fake):
    first = fake.commit_result(commit())
    again = fake.commit_result(commit())
    assert again.replayed and again.state_revision == first.state_revision == 1
    assert fake.get_state("run-1").state_revision == 1
    assert len(fake.history("run-1")) == 1


def test_same_operation_with_different_content_is_rejected(fake):
    fake.commit_result(commit())
    with pytest.raises(OperationConflict):
        fake.commit_result(commit(result_id="other"))


def test_wrong_expected_revision_conflicts_without_side_effects(fake):
    with pytest.raises(StateRevisionConflict) as e:
        fake.commit_result(commit(rev=3))
    assert (e.value.expected_revision, e.value.actual_revision) == (3, 0)
    assert fake.get_state("run-1").state_revision == 0 and fake.history("run-1") == []


def test_unknown_run(fake):
    with pytest.raises(RunNotFound):
        fake.get_state("nope")
    with pytest.raises(RunNotFound):
        fake.commit_result(commit().model_copy(update={"run_id": "nope"}))


def test_record_edit_commits_new_revision_and_marks_downstream_stale(fake):
    fake.commit_result(commit())
    fake.commit_result(commit(rev=1, op="op-2", agent="agent2", result_id="a2", upstream_result_ids=["r1"]))
    req = RecordEditRequest(run_id="run-1", expected_state_revision=2, operation_id="e1", agent_type="agent1",
                            target_result_id="r1", new_result_id="r1-edit", edited_by="pm-1",
                            edited_payload={"item": "x"}, reason="항목 보정")
    r = fake.record_edit(req)
    assert (r.state_revision, r.result_id, r.result_revision) == (3, "r1-edit", 2)
    s = fake.get_state("run-1")
    assert (s.agents["agent1"].current_result_id, s.agents["agent1"].result_revision) == ("r1-edit", 2)
    assert s.agents["agent2"].stale and s.agents["agent2"].current_result_id == "a2"
    old, new = fake.get_result("run-1", "r1"), fake.get_result("run-1", "r1-edit")
    assert old.result_payload == {"k": 1} and not old.is_current
    assert new.result_payload == {"item": "x"} and new.is_current and new.result_revision == 2
    assert fake.history("run-1")[-1]["edited_by"] == "pm-1"


def test_record_edit_validates_target_and_new_id(fake):
    fake.commit_result(commit())
    req = RecordEditRequest(run_id="run-1", expected_state_revision=1, operation_id="e1", agent_type="agent1",
                            target_result_id="old", new_result_id="n1", edited_by="pm-1", edited_payload={})
    with pytest.raises(InvalidStateTransition):
        fake.record_edit(req)
    reuse = req.model_copy(update={"operation_id": "e2", "target_result_id": "r1", "new_result_id": "r1"})
    with pytest.raises(InvalidStateTransition):
        fake.record_edit(reuse)
    assert fake.get_state("run-1").state_revision == 1


def test_record_edit_is_idempotent_and_conflicts_on_revision(fake):
    fake.commit_result(commit())
    req = RecordEditRequest(run_id="run-1", expected_state_revision=1, operation_id="e1", agent_type="agent1",
                            target_result_id="r1", new_result_id="n1", edited_by="pm-1", edited_payload={"a": 1})
    first = fake.record_edit(req)
    again = fake.record_edit(req)
    assert again.replayed and again.result_revision == first.result_revision == 2
    assert fake.get_state("run-1").state_revision == 2
    with pytest.raises(StateRevisionConflict):
        fake.record_edit(req.model_copy(update={"operation_id": "e2", "target_result_id": "n1",
                                                "new_result_id": "n2", "expected_state_revision": 1}))


def test_commit_marks_downstream_stale_and_resets_own(fake):
    fake.commit_result(commit())
    fake.commit_result(commit(rev=1, op="op-2", agent="agent2", result_id="a2", upstream_result_ids=["r1"]))
    fake.commit_result(commit(rev=2, op="op-3", agent="agent3", result_id="a3", upstream_result_ids=["r1", "a2"]))
    fake.commit_result(commit(rev=3, op="op-4", result_id="r1b"))
    s = fake.get_state("run-1")
    assert s.agents["agent2"].stale and s.agents["agent3"].stale and not s.agents["agent1"].stale
    fake.commit_result(commit(rev=4, op="op-5", agent="agent2", result_id="a2b", upstream_result_ids=["r1b"]))
    s = fake.get_state("run-1")
    assert not s.agents["agent2"].stale and s.agents["agent3"].stale


def test_get_result_exposes_created_at_and_is_current(fake):
    fake.commit_result(commit())
    r = fake.get_result("run-1", "r1")
    assert r.is_current and r.created_at is not None


def test_input_wait_is_distinct_from_pm_review(fake):
    fake.commit_result(commit())
    r = fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=1, operation_id="v1",
                                                 agent_type="agent1", reason="추가 정보 필요", kind="INPUT"))
    assert r.status == "WAITING_INPUT"
    s = fake.get_state("run-1")
    assert s.pending_review.kind == "INPUT" and s.agents["agent1"].status == "COMPLETED"
    pm = FakeRunStateClient()
    pm.create_run("run-2")
    pm.commit_result(commit().model_copy(update={"run_id": "run-2"}))
    pm.request_review(RequestReviewRequest(run_id="run-2", expected_state_revision=1, operation_id="v1",
                                           agent_type="agent1", reason="검토"))
    assert pm.get_state("run-2").status == "WAITING_PM_REVIEW" and pm.get_state("run-2").pending_review.kind == "PM_REVIEW"


def test_review_wait_transition_and_invalid_transitions(fake):
    with pytest.raises(InvalidStateTransition):
        fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=0, operation_id="v0",
                                                 agent_type="agent2", reason="x"))
    fake.commit_result(commit())
    r = fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=1, operation_id="v1",
                                                 agent_type="agent1", target_result_id="r1", reason="항목 확인"))
    assert r.status == "WAITING_PM_REVIEW"
    s = fake.get_state("run-1")
    assert s.pending_review.target_result_id == "r1"
    with pytest.raises(InvalidStateTransition):
        fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=2, operation_id="v2",
                                                 agent_type="agent1", reason="again"))
    fake.commit_result(commit(rev=2, op="op-2", result_id="r2"))
    assert fake.get_state("run-1").pending_review is None


def test_failed_result_marks_run_failed_and_rerun_allowed(fake):
    fake.commit_result(commit(execution_status="FAILED"))
    assert fake.get_state("run-1").status == "FAILED"
    fake.commit_result(commit(rev=1, op="op-2", result_id="r2"))
    assert fake.get_state("run-1").status == "RUNNING"


def test_persistence_failure_is_retryable_with_same_operation(fake):
    fake.fail_next_write()
    with pytest.raises(StatePersistenceError) as e:
        fake.commit_result(commit())
    assert e.value.retryable and fake.get_state("run-1").state_revision == 0
    assert fake.commit_result(commit()).state_revision == 1


def test_error_retryability_contract():
    retryable = {c.__name__: c.retryable for c in RunStateError.__subclasses__()}
    # http_run_state가 import되면 RunAccessDenied/RunRequestRejected(모두 재시도 불가)가 추가되므로 순서 독립으로 검증
    assert {k: v for k, v in retryable.items() if k not in ("RunAccessDenied", "RunRequestRejected")} == {
        "RunNotFound": False, "StateRevisionConflict": False, "OperationConflict": False,
        "InvalidStateTransition": False, "InvalidEditPayload": False, "StatePersistenceError": True}
    assert not retryable.get("RunAccessDenied") and not retryable.get("RunRequestRejected")


def test_running_and_skipped_are_representable_and_distinct_from_not_started(fake):
    fake.set_agent_status("run-1", "agent1", "RUNNING")
    fake.set_agent_status("run-1", "agent2", "SKIPPED")
    agents = fake.get_state("run-1").agents
    assert [agents[a].status for a in ("agent1", "agent2", "agent3")] == ["RUNNING", "SKIPPED", "NOT_STARTED"]
    fake.commit_result(commit())
    assert fake.get_state("run-1").agents["agent1"].status == "COMPLETED"


def test_failed_is_distinct_and_keeps_last_successful_result(fake):
    fake.commit_result(commit())
    fake.commit_result(commit(rev=1, op="op-2", result_id="r2", execution_status="FAILED"))
    agents = fake.get_state("run-1").agents
    assert agents["agent1"].status == "FAILED"
    assert (agents["agent1"].current_result_id, agents["agent1"].result_revision) == ("r1", 1)
    assert agents["agent2"].status == "NOT_STARTED"
    fake.commit_result(commit(rev=2, op="op-3", result_id="r3"))
    a1 = fake.get_state("run-1").agents["agent1"]
    assert (a1.status, a1.current_result_id, a1.result_revision) == ("COMPLETED", "r3", 3)


def test_review_wait_does_not_change_agent_status(fake):
    fake.commit_result(commit())
    before = fake.get_state("run-1").agents
    fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=1, operation_id="v1",
                                             agent_type="agent1", target_result_id="r1", reason="확인"))
    s = fake.get_state("run-1")
    assert s.status == "WAITING_PM_REVIEW" and s.pending_review.agent_type == "agent1"
    assert s.agents == before and s.agents["agent1"].status == "COMPLETED"


def test_snapshot_contract_validation():
    from pydantic import ValidationError
    from app.orchestration.run_state import AgentState, RunStateSnapshot
    with pytest.raises(ValidationError):
        AgentState(status="COMPLETED")
    with pytest.raises(ValidationError):
        AgentState(current_result_id="r1")
    with pytest.raises(ValidationError):
        RunStateSnapshot(run_id="r", state_revision=0, status="QUEUED", agents={"agent1": AgentState()})
    with pytest.raises(ValidationError):
        AgentState(status="WAITING_PM_REVIEW")


def resolve(rev, op="rv", decision="APPROVED", **kw):
    return ResolveReviewRequest(run_id="run-1", expected_state_revision=rev, operation_id=op, resolved_by="pm-1",
                                decision=decision, **kw)


@pytest.mark.parametrize("kind,waiting", [("PM_REVIEW", "WAITING_PM_REVIEW"), ("INPUT", "WAITING_INPUT")])
def test_resolve_review_returns_to_running_and_clears_pending(fake, kind, waiting):
    fake.commit_result(commit())
    fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=1, operation_id="v1",
                                             agent_type="agent1", reason="x", kind=kind))
    assert fake.get_state("run-1").status == waiting
    r = fake.resolve_review(resolve(2, comment="ok"))
    assert (r.status, r.state_revision, r.replayed) == ("RUNNING", 3, False)
    s = fake.get_state("run-1")
    assert s.pending_review is None and s.agents["agent1"].status == "COMPLETED"
    assert fake.resolve_review(resolve(2, comment="ok")).replayed is True
    assert fake.get_state("run-1").state_revision == 3


def test_resolve_review_rejects_without_pending_and_on_revision_conflict(fake):
    fake.commit_result(commit())
    with pytest.raises(InvalidStateTransition):
        fake.resolve_review(resolve(1))
    fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=1, operation_id="v1",
                                             agent_type="agent1", reason="x"))
    with pytest.raises(StateRevisionConflict):
        fake.resolve_review(resolve(1))
