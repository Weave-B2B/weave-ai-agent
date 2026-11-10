import pytest

from app.orchestration.fake_run_state import FakeRunStateClient
from app.orchestration.run_state import (
    CommitResultRequest, InvalidStateTransition, OperationConflict, ResultNotFound, RunNotFound, RunStateError,
    StartAgentRequest, StateRevisionConflict,
)


@pytest.fixture
def fake():
    f = FakeRunStateClient()
    f.create_run("run-1")
    return f


def start(agent="agent1", rev=0, op="s-1"):
    return StartAgentRequest(run_id="run-1", agent_type=agent, expected_state_revision=rev, operation_id=op)


def commit(agent, rev, op, rid, upstream=()):
    return CommitResultRequest(run_id="run-1", expected_state_revision=rev, operation_id=op, agent_type=agent,
                               result_id=rid, upstream_result_ids=list(upstream), result_payload={"id": rid})


def test_start_sets_running_and_increments_revision(fake):
    r = fake.start_agent(start())
    s = fake.get_state("run-1")
    assert (r.state_revision, r.replayed, s.state_revision, s.status) == (1, False, 1, "RUNNING")
    assert s.agents["agent1"].status == "RUNNING" and s.agents["agent2"].status == "NOT_STARTED"


def test_start_with_wrong_revision_conflicts_and_changes_nothing(fake):
    with pytest.raises(StateRevisionConflict):
        fake.start_agent(start(rev=5))
    assert fake.get_state("run-1").agents["agent1"].status == "NOT_STARTED"


def test_start_of_running_agent_is_rejected(fake):
    fake.start_agent(start())
    with pytest.raises(InvalidStateTransition):
        fake.start_agent(start(rev=1, op="s-2"))


def test_start_requires_completed_prerequisite(fake):
    with pytest.raises(InvalidStateTransition):
        fake.start_agent(start("agent2"))
    fake.start_agent(start())
    fake.commit_result(commit("agent1", 1, "c-1", "a1"))
    fake.start_agent(start("agent2", rev=2, op="s-2"))
    with pytest.raises(InvalidStateTransition):          # agent2는 아직 완료 전
        fake.start_agent(start("agent3", rev=3, op="s-3"))


def test_start_rejected_when_prerequisite_is_stale(fake):
    fake.start_agent(start())
    fake.commit_result(commit("agent1", 1, "c-1", "a1"))
    fake.simulate_stale("run-1", "agent1")
    with pytest.raises(InvalidStateTransition):
        fake.start_agent(start("agent2", rev=2, op="s-2"))


def test_start_idempotent_replay_and_operation_conflict(fake):
    first = fake.start_agent(start())
    replay = fake.start_agent(start(rev=99))             # expected revision은 멱등 비교에서 제외
    assert replay.replayed and replay.state_revision == first.state_revision == 1
    assert fake.get_state("run-1").state_revision == 1
    with pytest.raises(OperationConflict):
        fake.start_agent(start("agent2"))


def test_start_blocked_while_waiting_for_review(fake):
    from app.orchestration.run_state import RequestReviewRequest
    fake.start_agent(start())
    fake.commit_result(commit("agent1", 1, "c-1", "a1"))
    fake.request_review(RequestReviewRequest(run_id="run-1", expected_state_revision=2, operation_id="r-1",
                                             agent_type="agent1", target_result_id="a1", reason="x"))
    with pytest.raises(InvalidStateTransition):
        fake.start_agent(start("agent2", rev=3, op="s-2"))


def test_get_result_returns_committed_payload(fake):
    fake.start_agent(start())
    fake.commit_result(commit("agent1", 1, "c-1", "a1"))
    res = fake.get_result("run-1", "a1")
    assert (res.agent_type, res.result_revision, res.result_payload) == ("agent1", 1, {"id": "a1"})
    with pytest.raises(ResultNotFound) as e:
        fake.get_result("run-1", "nope")
    assert isinstance(e.value, RunNotFound) and e.value.retryable is False
    with pytest.raises(RunNotFound):
        fake.get_result("zzz", "a1")


def test_new_error_is_not_retryable_and_hierarchy_kept():
    assert issubclass(ResultNotFound, RunStateError) and ResultNotFound.retryable is False
