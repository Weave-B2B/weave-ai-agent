"""RunStateClient 공통 계약 테스트. 같은 시나리오를 Fake와 실제 Backend(HttpRunStateClient)에 돌린다.

live 변형은 WEAVE_BACKEND_URL / WEAVE_INTERNAL_TOKEN / WEAVE_ACTOR_MEMBER_ID / WEAVE_PROJECT_ID가 있을 때만 실행된다.
(Backend 쪽 기동기: weave-backend ContractServerLauncherTest, 포트 18089, 토큰 contract-token)
Run은 변경요청당 하나이므로 live에서는 테스트마다 새 변경요청 ID로 Run을 만든다.
"""
import os
import uuid

import pytest

from app.orchestration.fake_run_state import FakeRunStateClient
from app.orchestration.http_run_state import HttpRunStateClient
from app.orchestration.run_state import (
    CommitResultRequest, InvalidStateTransition, OperationConflict, RecordEditRequest, RequestReviewRequest,
    ResolveReviewRequest, ResultNotFound, RunNotFound, StartAgentRequest, StateRevisionConflict,
)

ACTOR = os.environ.get("WEAVE_ACTOR_MEMBER_ID", str(uuid.UUID(int=0xB1)))
LIVE_ENV = ("WEAVE_BACKEND_URL", "WEAVE_INTERNAL_TOKEN", "WEAVE_ACTOR_MEMBER_ID", "WEAVE_PROJECT_ID")


@pytest.fixture(params=["fake", "live"])
def ctx(request):
    if request.param == "fake":
        client = FakeRunStateClient(strict_commit=True)
        client.create_run("run-1")
        yield client, "run-1"
        return
    if not all(os.environ.get(k) for k in LIVE_ENV):
        pytest.skip("실제 Backend 환경변수(WEAVE_BACKEND_URL 등)가 없어 live 계약 테스트를 건너뜀")
    client = HttpRunStateClient(os.environ["WEAVE_BACKEND_URL"], os.environ["WEAVE_INTERNAL_TOKEN"], ACTOR)
    run_id = client.create_run(os.environ["WEAVE_PROJECT_ID"], str(uuid.uuid4())).run_id
    yield client, run_id
    client.close()


class Flow:
    """revision과 operation_id를 자동으로 관리하는 시나리오 도우미."""

    def __init__(self, client, run_id):
        self.c, self.run_id = client, run_id
        self.n = 0

    def op(self):
        self.n += 1
        return f"op-{uuid.uuid4().hex[:8]}-{self.n}"

    def rev(self):
        return self.c.get_state(self.run_id).state_revision

    def start(self, agent, **kw):
        return self.c.start_agent(StartAgentRequest(
            run_id=self.run_id, agent_type=agent, expected_state_revision=kw.pop("rev", None) or self.rev(),
            operation_id=kw.pop("op", None) or self.op()))

    def commit(self, agent, result_id, upstream=(), **kw):
        return self.c.commit_result(CommitResultRequest(
            run_id=self.run_id, expected_state_revision=self.rev(), operation_id=self.op(), agent_type=agent,
            result_id=result_id, upstream_result_ids=list(upstream), result_payload={"k": result_id}, **kw))

    def run_agent(self, agent, result_id, upstream=()):
        self.start(agent)
        return self.commit(agent, result_id, upstream)


def test_initial_state(ctx):
    client, run_id = ctx
    s = client.get_state(run_id)
    assert (s.state_revision, s.status, s.pending_review) == (0, "QUEUED", None)
    assert set(s.agents) == {"agent1", "agent2", "agent3"}
    assert all(a.status == "NOT_STARTED" and a.current_result_id is None and not a.stale for a in s.agents.values())


def test_start_commit_get_result(ctx):
    f = Flow(*ctx)
    started = f.start("agent1")
    assert (started.state_revision, started.status, started.replayed) == (1, "RUNNING", False)
    assert f.c.get_state(f.run_id).agents["agent1"].status == "RUNNING"
    committed = f.commit("agent1", "r1")
    assert (committed.state_revision, committed.result_id, committed.result_revision) == (2, "r1", 1)
    s = f.c.get_state(f.run_id)
    a1 = s.agents["agent1"]
    assert (a1.status, a1.current_result_id, a1.result_revision) == ("COMPLETED", "r1", 1)
    r = f.c.get_result(f.run_id, "r1")
    assert r.is_current and r.result_payload == {"k": "r1"} and r.result_revision == 1 and r.created_at


def test_same_operation_replays_without_double_apply(ctx):
    f = Flow(*ctx)
    first = f.start("agent1", op="op-replay")
    again = f.c.start_agent(StartAgentRequest(run_id=f.run_id, agent_type="agent1", expected_state_revision=0,
                                              operation_id="op-replay"))
    assert again.replayed is True and again.state_revision == first.state_revision
    assert f.rev() == 1


def test_operation_id_reused_for_other_request_conflicts(ctx):
    f = Flow(*ctx)
    f.start("agent1", op="op-dup")
    with pytest.raises(OperationConflict):
        f.c.start_agent(StartAgentRequest(run_id=f.run_id, agent_type="agent2", expected_state_revision=0,
                                          operation_id="op-dup"))


def test_wrong_expected_revision_conflicts_with_actual(ctx):
    f = Flow(*ctx)
    with pytest.raises(StateRevisionConflict) as e:
        f.start("agent1", rev=9)
    assert e.value.actual_revision == 0 and not e.value.retryable


def test_unknown_run_and_result(ctx):
    f = Flow(*ctx)
    with pytest.raises(ResultNotFound):
        f.c.get_result(f.run_id, "nope")
    with pytest.raises(RunNotFound):
        f.c.get_state(str(uuid.uuid4()) if not isinstance(f.c, FakeRunStateClient) else "missing")


def test_prerequisite_and_running_guards(ctx):
    f = Flow(*ctx)
    with pytest.raises(InvalidStateTransition):
        f.start("agent2")
    f.start("agent1")
    with pytest.raises(InvalidStateTransition):
        f.start("agent1")


def test_result_id_cannot_be_reused(ctx):
    f = Flow(*ctx)
    f.run_agent("agent1", "r1")
    f.start("agent1")
    with pytest.raises(InvalidStateTransition):
        f.commit("agent1", "r1")


def test_commit_requires_running_agent_and_matching_upstream(ctx):
    f = Flow(*ctx)
    with pytest.raises(InvalidStateTransition):
        f.commit("agent1", "r1")
    f.run_agent("agent1", "r1")
    f.start("agent2")
    with pytest.raises(InvalidStateTransition):
        f.commit("agent2", "r2", upstream=["wrong"])


def test_edit_creates_new_revision_and_marks_downstream_stale(ctx):
    f = Flow(*ctx)
    f.run_agent("agent1", "a1")
    f.run_agent("agent2", "a2", upstream=["a1"])
    receipt = f.c.record_edit(RecordEditRequest(
        run_id=f.run_id, expected_state_revision=f.rev(), operation_id=f.op(), agent_type="agent1",
        target_result_id="a1", new_result_id="a1-edit", edited_by=ACTOR, edited_payload={"k": "edited"},
        reason="수정"))
    assert (receipt.result_id, receipt.result_revision) == ("a1-edit", 2)
    s = f.c.get_state(f.run_id)
    assert s.agents["agent1"].current_result_id == "a1-edit"
    assert s.agents["agent2"].stale is True and s.agents["agent2"].current_result_id == "a2"
    assert not f.c.get_result(f.run_id, "a1").is_current
    assert f.c.get_result(f.run_id, "a1-edit").result_payload == {"k": "edited"}
    with pytest.raises(InvalidStateTransition):
        f.start("agent3")


def test_failed_commit_keeps_previous_success_pointer(ctx):
    f = Flow(*ctx)
    f.run_agent("agent1", "r1")
    f.start("agent1")
    receipt = f.commit("agent1", "r1-fail", execution_status="FAILED")
    assert receipt.status == "FAILED"
    a1 = f.c.get_state(f.run_id).agents["agent1"]
    assert (a1.status, a1.current_result_id) == ("FAILED", "r1")


@pytest.mark.parametrize("kind,waiting", [("PM_REVIEW", "WAITING_PM_REVIEW"), ("INPUT", "WAITING_INPUT")])
def test_review_wait_blocks_start_until_resolved(ctx, kind, waiting):
    f = Flow(*ctx)
    f.start("agent1")
    r = f.c.request_review(RequestReviewRequest(
        run_id=f.run_id, expected_state_revision=f.rev(), operation_id=f.op(), agent_type="agent1",
        reason="확인", kind=kind))
    assert r.status == waiting
    s = f.c.get_state(f.run_id)
    assert s.status == waiting and s.pending_review.kind == kind and s.pending_review.reason == "확인"
    with pytest.raises(InvalidStateTransition):
        f.start("agent2")
    resolved = f.c.resolve_review(ResolveReviewRequest(
        run_id=f.run_id, expected_state_revision=f.rev(), operation_id=f.op(), resolved_by=ACTOR,
        decision="APPROVED", comment="ok"))
    assert resolved.status == "RUNNING"
    assert f.c.get_state(f.run_id).pending_review is None
    with pytest.raises(InvalidStateTransition):
        f.c.resolve_review(ResolveReviewRequest(
            run_id=f.run_id, expected_state_revision=f.rev(), operation_id=f.op(), resolved_by=ACTOR,
            decision="APPROVED"))


def test_resolve_to_completed_only_when_all_agents_current_and_approved(ctx):
    f = Flow(*ctx)
    f.run_agent("agent1", "r1")
    f.start("agent2")
    f.c.request_review(RequestReviewRequest(run_id=f.run_id, expected_state_revision=f.rev(), operation_id=f.op(),
                                            agent_type="agent2", reason="검토"))

    def resolve(decision, target):
        return f.c.resolve_review(ResolveReviewRequest(
            run_id=f.run_id, expected_state_revision=f.rev(), operation_id=f.op(), resolved_by=ACTOR,
            decision=decision, requested_run_status=target))
    with pytest.raises(InvalidStateTransition):       # agent2/3가 아직 COMPLETED가 아님
        resolve("APPROVED", "COMPLETED")
    assert f.c.get_state(f.run_id).pending_review is not None
    assert resolve("APPROVED", "RUNNING").status == "RUNNING"
