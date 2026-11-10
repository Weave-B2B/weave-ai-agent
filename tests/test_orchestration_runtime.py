"""Orchestration Runtime: Fake RunStateClient + fixture/Mock Agent로 흐름·입력·오류 처리를 검증한다.
실제 LLM·Backend는 호출하지 않는다. 통과는 Backend 연동 검증이 아니다."""
import itertools

import pytest
from schema.schemas import Agent1Input, Agent1Output, Agent2Output, AgentError, PMReview

from app.agents.base import AgentFailure, AgentSet
from app.agents.fixture import FixtureAgent1, FixtureAgent2, FixtureAgent3, load_chain
from app.orchestration.fake_run_state import FakeRunStateClient
from app.orchestration.run_state import RequestReviewRequest, StartAgentRequest, CommitResultRequest
from app.orchestration.runtime import OrchestrationRuntime, RunInputs

RC = "RC-SYNTH-2026H2-v1"
CASE = "CR-A-001"
RUN = "run-1"


class Rec:
    """Fixture 에이전트를 감싸 호출 순서와 입력을 기록한다. 필요 시 예외를 낸다."""
    def __init__(self, name, inner, log, fail=None):
        self.name, self.inner, self.log, self.fail, self.inputs = name, inner, log, fail, []

    def run(self, inp):
        self.log.append(self.name)
        self.inputs.append(inp)
        if self.fail:
            raise self.fail
        return self.inner.run(inp)


class Provider:
    """Backend에 저장돼 있다고 가정하는 최초 입력. PM 검토는 사용자가 한 뒤에만 제공된다."""
    def __init__(self, fake, row):
        self.fake, self.row, self.reviewed = fake, row, False

    def load(self, run_id):
        a2_id = self.fake.get_state(run_id).agents["agent2"].current_result_id
        rv = None
        if self.reviewed and a2_id:
            rv = PMReview.model_validate({**self.row["pm_review"], "target_result_id": a2_id})
        return RunInputs(agent1_input=Agent1Input.model_validate(self.row["agent1_input"]), rate_card_id=RC, pm_review=rv)


@pytest.fixture
def env():
    chain = load_chain()
    row = chain[CASE]
    fake = FakeRunStateClient()
    fake.create_run(RUN)
    log: list[str] = []
    ags = {n: Rec(n, cls(chain), log) for n, cls in (("agent1", FixtureAgent1), ("agent2", FixtureAgent2), ("agent3", FixtureAgent3))}
    prov = Provider(fake, row)
    counter = itertools.count(1)
    rt = OrchestrationRuntime(fake, AgentSet(ags["agent1"], ags["agent2"], ags["agent3"]), prov,
                              operation_id_factory=lambda: f"op-{next(counter)}")
    return type("Env", (), dict(fake=fake, log=log, ags=ags, prov=prov, rt=rt, chain=chain, row=row))


def finish_review(env):
    env.prov.reviewed = True
    env.fake.simulate_review_resolved(RUN)


def run_to_review(env):
    res = env.rt.run(RUN)
    assert res.outcome == "WAITING_FOR_REVIEW"
    return res


def test_1_normal_flow_a1_a2_a3_with_review_gate(env):
    res = run_to_review(env)
    assert env.log == ["agent1", "agent2"]
    assert res.pending_review.agent_type == "agent2" and res.run_status == "WAITING_PM_REVIEW"
    finish_review(env)
    res = env.rt.run(RUN)
    assert env.log == ["agent1", "agent2", "agent3"]
    assert (res.outcome, res.run_status, [e.agent_type for e in res.executed]) == ("COMPLETED", "COMPLETED", ["agent3"])
    s = env.fake.get_state(RUN)
    assert all(a.status == "COMPLETED" for a in s.agents.values())
    assert "result_payload" not in res.model_dump_json()


def test_agent_review_state_is_not_agent_status(env):
    run_to_review(env)
    s = env.fake.get_state(RUN)
    assert s.agents["agent2"].status == "COMPLETED" and s.pending_review is not None and s.status == "WAITING_PM_REVIEW"


def test_2_a1_failure_stops_rest(env):
    env.ags["agent1"].fail = AgentFailure(AgentError(code="MODEL_TIMEOUT", message="t", stage="agent1", retryable=True))
    res = env.rt.run(RUN)
    assert (res.outcome, res.error.code, env.log) == ("FAILED", "MODEL_TIMEOUT", ["agent1"])
    s = env.fake.get_state(RUN)
    assert s.agents["agent1"].status == "FAILED" and s.agents["agent2"].status == "NOT_STARTED" and s.status == "FAILED"


def test_3_a2_failure_stops_a3_and_12_keeps_previous_pointer(env):
    env.ags["agent2"].fail = RuntimeError("boom")
    res = env.rt.run(RUN)
    assert (res.outcome, res.error.code, env.log) == ("FAILED", "INTERNAL_ERROR", ["agent1", "agent2"])
    s = env.fake.get_state(RUN)
    assert s.agents["agent2"].status == "FAILED" and s.agents["agent3"].status == "NOT_STARTED"
    # 12: 성공 결과가 있는 상태에서 재실행이 실패하면 기존 current 포인터를 유지한다
    env.ags["agent2"].fail = None
    run_to_review(env)
    good = env.fake.get_state(RUN).agents["agent2"].current_result_id
    env.fake.set_agent_status(RUN, "agent2", "COMPLETED")
    env.fake.simulate_stale(RUN, "agent2")
    env.fake.simulate_review_resolved(RUN)
    env.ags["agent2"].fail = RuntimeError("again")
    res = env.rt.run(RUN)
    s = env.fake.get_state(RUN)
    assert res.outcome == "FAILED" and s.agents["agent2"].status == "FAILED" and s.agents["agent2"].current_result_id == good


def test_4_a2_not_run_before_a1_commit(env):
    seen = []
    orig = env.fake.commit_result

    def spy(req):
        seen.append(("commit", req.agent_type))
        return orig(req)
    env.fake.commit_result = spy
    a2 = env.ags["agent2"]
    orig_run = a2.run
    a2.run = lambda inp: (seen.append(("run", "agent2")), orig_run(inp))[1]
    run_to_review(env)
    assert seen.index(("commit", "agent1")) < seen.index(("run", "agent2"))


def test_5_a2_receives_latest_committed_a1_and_6_a3_receives_a1_a2(env):
    run_to_review(env)
    s = env.fake.get_state(RUN)
    a1_id, a2_id = s.agents["agent1"].current_result_id, s.agents["agent2"].current_result_id
    assert env.ags["agent2"].inputs[0].a1_result_id == a1_id
    committed_a1 = env.fake.get_result(RUN, a1_id).result_payload
    assert [i.item_id for i in env.ags["agent2"].inputs[0].items] <= [i["item_id"] for i in committed_a1["items"]]
    finish_review(env)
    env.rt.run(RUN)
    a3_in = env.ags["agent3"].inputs[0]
    assert a3_in.a2_result.meta.result_id == a2_id
    s = env.fake.get_state(RUN)
    assert s.agents["agent3"].upstream_result_ids == [a1_id, a2_id]


def test_7_stops_at_review_and_8_14_no_rerun_of_completed(env):
    run_to_review(env)
    n = list(env.log)
    res = env.rt.run(RUN)                       # 검토 전 재호출: 아무것도 실행하지 않는다
    assert env.log == n and res.outcome == "WAITING_FOR_REVIEW" and res.executed == []
    finish_review(env)
    env.rt.run(RUN)
    assert env.log == ["agent1", "agent2", "agent3"]
    again = env.rt.run(RUN)                     # 완료 후 재호출
    assert env.log == ["agent1", "agent2", "agent3"] and again.outcome == "COMPLETED" and again.executed == []


def test_9_start_revision_conflict_rereads_state_and_retries(env):
    env.fake.simulate_concurrent_write(only=StartAgentRequest)
    res = env.rt.run(RUN)
    assert res.outcome == "WAITING_FOR_REVIEW" and env.log == ["agent1", "agent2"]
    starts = [h for h in env.fake.history(RUN) if h["event"] == "agent_started" and h["agent_type"] == "agent1"]
    assert len(starts) == 1


def test_9b_start_conflict_where_agent_no_longer_runnable_stops(env):
    # 충돌 사이에 다른 주체가 agent1을 시작해 버린 상황 → 재조회 후 실행하지 않고 멈춘다
    orig = env.fake.start_agent

    def racing(req):
        if req.operation_id == "op-1":
            orig(StartAgentRequest(run_id=RUN, agent_type="agent1", expected_state_revision=0, operation_id="other"))
        return orig(req)
    env.fake.start_agent = racing
    res = env.rt.run(RUN)
    assert res.outcome == "STOPPED" and res.stop_reason == "AGENT_RUNNING" and env.log == []


def test_10_duplicate_start_does_not_execute_twice(env):
    orig = env.fake.start_agent
    env.fake.start_agent = lambda req: orig(req).model_copy(update={"replayed": True})
    res = env.rt.run(RUN)
    assert (res.outcome, res.stop_reason, env.log) == ("STOPPED", "START_REPLAYED", [])


def test_11_transient_commit_failure_retries_with_same_operation_id(env):
    env.fake.fail_next_write(only=CommitResultRequest)
    ops = []
    orig = env.fake.commit_result
    env.fake.commit_result = lambda req: (ops.append(req.operation_id), orig(req))[1]
    run_to_review(env)
    assert env.log == ["agent1", "agent2"]       # 재시도로 에이전트를 다시 실행하지 않는다
    assert ops[0] == ops[1]
    assert len([h for h in env.fake.history(RUN) if h["event"] == "result_committed" and h["agent_type"] == "agent1"]) == 1


def test_commit_revision_conflict_does_not_rerun_agent(env):
    env.fake.simulate_concurrent_write(only=CommitResultRequest)
    run_to_review(env)
    assert env.log == ["agent1", "agent2"]
    assert env.fake.get_state(RUN).agents["agent1"].status == "COMPLETED"


def test_persistent_commit_failure_stops_without_next_agent(env):
    def always_fail(req):
        from app.orchestration.run_state import StatePersistenceError
        raise StatePersistenceError("down")
    env.fake.commit_result = always_fail
    res = env.rt.run(RUN)
    assert (res.outcome, res.stop_reason, env.log) == ("STOPPED", "COMMIT_FAILED", ["agent1"])


def test_13_stale_prerequisite_blocks_and_is_rerun_first(env):
    run_to_review(env)
    finish_review(env)
    env.fake.simulate_stale(RUN, "agent1")
    res = env.rt.run(RUN)
    # 고정 순서에서 stale인 agent1부터 다시 실행된다(하위 Agent는 선행이 최신이 되기 전까지 시작하지 않는다)
    assert env.log[:2] == ["agent1", "agent2"] and env.log[2:3] == ["agent1"]
    assert env.fake.get_state(RUN).agents["agent1"].stale is False
    from app.orchestration.workflow_rules import blocking_reason
    s = env.fake.get_state(RUN)
    env.fake.simulate_stale(RUN, "agent1")
    assert "stale" in blocking_reason(env.fake.get_state(RUN), "agent2")


def test_running_agent_is_not_recovered_automatically(env):
    env.fake.start_agent(StartAgentRequest(run_id=RUN, agent_type="agent1", expected_state_revision=0, operation_id="x"))
    res = env.rt.run(RUN)
    assert (res.outcome, res.stop_reason, env.log) == ("STOPPED", "AGENT_RUNNING", [])
    assert env.fake.get_state(RUN).agents["agent1"].status == "RUNNING"


def test_missing_review_requests_pm_review_without_running_a3(env):
    run_to_review(env)
    assert "agent3" not in env.log
    assert env.fake.get_state(RUN).pending_review.target_result_id == env.fake.get_state(RUN).agents["agent2"].current_result_id


def test_needs_input_waits_as_input_not_pm_review(env):
    inner = env.ags["agent1"].inner
    original = inner.run
    inner.run = lambda inp: original(inp).model_copy(update={"status": "NEEDS_INPUT"})
    res = env.rt.run(RUN)
    s = env.fake.get_state(RUN)
    assert res.outcome == "WAITING_FOR_REVIEW" and env.log == ["agent1"]
    assert s.status == "WAITING_INPUT" and s.pending_review.kind == "INPUT"
    assert s.agents["agent1"].status == "COMPLETED"


# ---------------------------------------------------------------- resume (resolve_review 경유 재개)
def test_resume_approved_resolves_via_backend_and_continues_to_a3(env):
    run_to_review(env)
    env.prov.reviewed = True                      # PM 검토 결과는 Provider(Backend 저장분)가 제공한다고 가정
    res = env.rt.resume(RUN, "pm-1", "APPROVED", "ok")
    assert env.log == ["agent1", "agent2", "agent3"]
    assert (res.outcome, res.run_status) == ("COMPLETED", "COMPLETED")
    history = [h["event"] for h in env.fake.history(RUN)]
    assert "review_resolved" in history


def test_resume_without_review_data_waits_again_not_runs_a3(env):
    run_to_review(env)
    res = env.rt.resume(RUN, "pm-1", "APPROVED")     # Provider가 PM 검토 결과를 아직 주지 않음
    assert env.log == ["agent1", "agent2"] and res.outcome == "WAITING_FOR_REVIEW"


def test_resume_rejected_resolves_and_stops_without_running(env):
    run_to_review(env)
    res = env.rt.resume(RUN, "pm-1", "REJECTED", "다시")
    assert (res.outcome, res.stop_reason, res.run_status) == ("STOPPED", "REVIEW_REJECTED", "RUNNING")
    assert env.log == ["agent1", "agent2"] and env.fake.get_state(RUN).pending_review is None


def test_resume_not_waiting_is_noop(env):
    res = env.rt.resume(RUN, "pm-1", "APPROVED")
    assert (res.outcome, res.stop_reason) == ("STOPPED", "NOT_WAITING_FOR_REVIEW") and env.log == []


def test_resume_input_wait_is_not_resolved(env):
    env.fake.start_agent(StartAgentRequest(run_id=RUN, agent_type="agent1", expected_state_revision=0, operation_id="s1"))
    env.fake.request_review(RequestReviewRequest(run_id=RUN, expected_state_revision=1, operation_id="r1",
                                                 agent_type="agent1", reason="입력", kind="INPUT"))
    res = env.rt.resume(RUN, "pm-1", "APPROVED")
    assert (res.outcome, res.stop_reason, res.run_status) == ("STOPPED", "INPUT_RESUME_UNSUPPORTED", "WAITING_INPUT")
    assert env.fake.get_state(RUN).pending_review.kind == "INPUT"


def _all_done_waiting_on_a3_review(env):
    f, n = env.fake, itertools.count(1)
    for agent, rid, up in (("agent1", "r1", []), ("agent2", "r2", ["r1"]), ("agent3", "r3", ["r2"])):
        rev = f.get_state(RUN).state_revision
        f.start_agent(StartAgentRequest(run_id=RUN, agent_type=agent, expected_state_revision=rev, operation_id=f"s{next(n)}"))
        f.commit_result(CommitResultRequest(run_id=RUN, expected_state_revision=rev + 1, operation_id=f"c{next(n)}",
                                            agent_type=agent, result_id=rid, upstream_result_ids=up, result_payload={}))
    rev = f.get_state(RUN).state_revision
    f.request_review(RequestReviewRequest(run_id=RUN, expected_state_revision=rev, operation_id="rv", agent_type="agent3",
                                          target_result_id="r3", reason="agent3 검토 필요"))


def test_resume_approving_last_agent_review_completes_run_without_rerun(env):
    _all_done_waiting_on_a3_review(env)
    res = env.rt.resume(RUN, "pm-1", "APPROVED")
    assert (res.outcome, res.run_status, res.executed, env.log) == ("COMPLETED", "COMPLETED", [], [])


def test_resume_rejecting_last_agent_review_does_not_complete_run(env):
    _all_done_waiting_on_a3_review(env)
    res = env.rt.resume(RUN, "pm-1", "REJECTED")
    assert (res.stop_reason, res.run_status) == ("REVIEW_REJECTED", "RUNNING")


def test_fake_rejects_completed_resolve_when_not_all_agents_current(env):
    run_to_review(env)
    snap = env.fake.get_state(RUN)
    from app.orchestration.run_state import InvalidStateTransition, ResolveReviewRequest
    with pytest.raises(InvalidStateTransition):
        env.fake.resolve_review(ResolveReviewRequest(
            run_id=RUN, expected_state_revision=snap.state_revision, operation_id="x", resolved_by="pm",
            decision="APPROVED", requested_run_status="COMPLETED"))
