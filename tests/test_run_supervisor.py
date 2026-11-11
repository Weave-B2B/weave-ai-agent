"""Run-backed orchestrator integration with synthetic workers and no live API."""
import copy

import pytest

from test_agent3_agent import a3_payload
from test_agent2_orchestrator_adapter import CountingAgent
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import FAKE_EMAIL
from app.agents.base import AgentSet
from app.agents.fixture import FixtureAgent1, load_chain
from app.llm.fake import FakeLlm
from app.orchestration.fake_run_coordination import FakeCoordinatedRunStateClient
from app.orchestration.run_coordination import AnswerQuestion, FinalReviewDecision
from app.orchestration.run_state import RunInputs, StateRevisionConflict
from app.orchestration.run_supervisor import RunSupervisorRuntime
from schema.schemas import Agent1Input, Question


@pytest.fixture
def setup(a3_payload):
    chain = load_chain()
    inp = Agent1Input.model_validate(chain["CR-A-001"]["agent1_input"])
    state = FakeCoordinatedRunStateClient()
    state.create_run("run-1", RunInputs(agent1_input=inp, rate_card_id="RC-SYNTH-2026H2-v1"))
    scope = CountingAgent("CR-A-001")
    worker_llm = FakeLlm([copy.deepcopy(a3_payload)] * 8)
    workers = AgentSet(FixtureAgent1(chain), scope, LlmAgent3(worker_llm))
    ids = ["CR-A-001-1"]
    decisions = FakeLlm([
        {"action": "assess_scope", "args": {"item_ids": ids}, "reason": "범위 확인"},
        {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ids}, "reason": "견적 연결"},
        {"action": "estimate_impact", "args": {"item_ids": ids}, "reason": "잠정 산정"},
        {"action": "draft_documents", "args": {"item_ids": ids + ["CR-A-001-3"], "kind": "negotiation_email"}, "reason": "초안"},
    ])
    draft = FakeLlm([copy.deepcopy(FAKE_EMAIL)] * 4)
    return state, workers, decisions, draft


def runtime(setup):
    return RunSupervisorRuntime(*setup)


def test_new_runtime_per_step_reads_run_and_reuses_impact_for_drafting(setup):
    state, workers, decisions, draft = setup
    results = [runtime(setup).step("run-1") for _ in range(6)]
    assert [r.get("action") for r in results[:5]] == [
        "analyze_request", "assess_scope", "link_quote", "estimate_impact", "draft_documents"]
    assert results[-1]["status"] == "waiting_final_review"
    run = state.get_coordinated_run("run-1")
    assert run.state.status == "WAITING_PM_REVIEW"
    assert len(workers.agent3.llm.calls) == 1  # restoring for documents did not call estimation again
    assert len(draft.calls) == 1 and len(decisions.calls) == 4
    payload = state.get_result("run-1", run.state.agents["agent3"].current_result_id).result_payload
    assert payload["documents"]["email"]["body"] and payload["documents"]["agreement"]["body"]
    assert "session_id" not in str(payload) and "a2_result" not in payload
    assert payload["source"]["a2_result_id"] == run.state.agents["agent2"].current_result_id
    for stage in ("agent1", "agent2"):
        rid = run.state.agents[stage].current_result_id
        assert state.get_result("run-1", rid).result_payload["meta"]["result_id"] == rid
    receipt = runtime(setup).review(FinalReviewDecision(run_id="run-1", operation_id="approve",
        expected_state_revision=run.state.state_revision, bundle=run.review_bundle,
        reviewed_by="pm", decision="approve"))
    assert receipt.status == "COMPLETED"
    assert runtime(setup).step("run-1")["status"] == "completed"


def test_agent2_pm_question_stops_before_agent3_and_answer_reaches_new_worker(setup):
    state, workers, decisions, draft = setup
    original = workers.agent2.run
    seen_answers = []
    def scope_with_question(inp, focus=None):
        seen_answers.append(list(inp.answers))
        out = original(inp, focus=focus)
        if not inp.answers:
            out.questions.append(Question(question_id="reuse-q", case_id=inp.context.case_id, stage="agent2",
                item_ids=["CR-A-001-1"], field="photo_reuse", text="기존 사진 기능을 재사용할 수 있나요?",
                reason="개발 담당 확인", target="pm", blocking=False))
        return out
    workers.agent2.run = scope_with_question
    runtime(setup).step("run-1")
    runtime(setup).step("run-1")
    waiting = runtime(setup).step("run-1")
    assert waiting["status"] == "waiting_input"
    assert not workers.agent3.llm.calls
    run = waiting["run"]
    req = AnswerQuestion(run_id="run-1", expected_state_revision=run.state.state_revision,
        operation_id="answer", question_id="reuse-q", answer="기존 업로드 API를 재사용합니다", answered_by="pm")
    runtime(setup).answer(req)
    # After a PM answer the orchestrator must re-check scope, not consume the old link-quote decision.
    new_decision = FakeLlm([{"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "답변 반영"}])
    next_runtime = RunSupervisorRuntime(state, workers, new_decision, draft)
    assert next_runtime.step("run-1")["action"] == "assess_scope"
    assert seen_answers[-1][0].text == req.answer
    assert state.get_coordinated_run("run-1").coordination.answers[0].question_id == "reuse-q"


def test_unknown_answer_does_not_call_more_models(setup):
    state, workers, decisions, draft = setup
    from app.orchestration.run_coordination import CoordinationState, PMQuestion, SaveCoordination
    runtime(setup).step("run-1")
    run = state.get_coordinated_run("run-1")
    coord = run.coordination.model_copy(deep=True)
    coord.questions = [PMQuestion(question_id="q1", item_ids=["CR-A-001-1"], field="photo_reuse", text="재사용 가능한가요?")]
    state.save_coordination(SaveCoordination(run_id="run-1", expected_state_revision=run.state.state_revision,
        operation_id="question", state=coord))
    run = state.get_coordinated_run("run-1")
    runtime(setup).answer(AnswerQuestion(run_id="run-1", expected_state_revision=run.state.state_revision,
        operation_id="unknown", question_id="q1", answer="모름", disposition="unknown", answered_by="pm"))
    assert runtime(setup).step("run-1")["status"] == "waiting_input"
    assert not decisions.calls and not workers.agent3.llm.calls


def test_old_answer_revision_is_rejected(setup):
    from app.orchestration.run_coordination import CoordinationState, PMQuestion, SaveCoordination
    state = setup[0]
    state.save_coordination(SaveCoordination(run_id="run-1", expected_state_revision=0, operation_id="question",
        state=CoordinationState(questions=[PMQuestion(question_id="q1", item_ids=["CR-A-001-1"], field="x", text="확인?")])))
    with pytest.raises(StateRevisionConflict):
        runtime(setup).answer(AnswerQuestion(run_id="run-1", expected_state_revision=0, operation_id="old",
            question_id="q1", answer="확인", answered_by="pm"))


def test_research_without_new_evidence_keeps_result_ids_then_asks_pm(setup):
    from app.agents.agent3.contracts import Agent3Need
    state, workers, _, draft = setup
    original = workers.agent3.run
    def missing_evidence(inp):
        output = original(inp)
        output.status = "NEEDS_INPUT"
        output.needs = [Agent3Need(kind="more_evidence", item_ids=["CR-A-001-1"], topic="사진 저장소 재사용 조건")]
        return output
    workers.agent3.run = missing_evidence
    decisions = FakeLlm([
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "범위 확인"},
        {"action": "link_quote", "args": {"quote_item_id": "CR-A-001-3", "item_ids": ["CR-A-001-1"]}, "reason": "견적 연결"},
        {"action": "estimate_impact", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "산정"},
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"], "focus": "사진 저장소 재사용 조건"}, "reason": "추가 조사"},
    ])
    args = state, workers, decisions, draft
    for _ in range(4):
        runtime(args).step("run-1")
    before = state.get_state("run-1")
    assert runtime(args).step("run-1")["action"] == "assess_scope"
    after = state.get_state("run-1")
    assert before.agents["agent2"].current_result_id == after.agents["agent2"].current_result_id
    assert before.agents["agent3"].current_result_id == after.agents["agent3"].current_result_id
    assert after.agents["agent2"].status == "COMPLETED"
    waiting = runtime(args).step("run-1")
    assert waiting["status"] == "waiting_input"
    assert "재사용" in waiting["run"].coordination.questions[0].text
    assert len(workers.agent2.calls) == 2 and len(workers.agent3.llm.calls) == 1


def test_duplicate_step_revision_does_not_execute_another_agent(setup):
    runtime(setup).step("run-1", expected_revision=0)
    with pytest.raises(StateRevisionConflict):
        runtime(setup).step("run-1", expected_revision=0)
    assert not setup[2].calls


def test_step_limit_stops_before_next_model_call(setup):
    from app.orchestration.run_coordination import SaveCoordination
    state = setup[0]
    run = state.get_coordinated_run("run-1")
    coord = run.coordination.model_copy(deep=True)
    coord.segment_steps = 12
    state.save_coordination(SaveCoordination(run_id="run-1", expected_state_revision=0,
        operation_id="cap", state=coord))
    assert runtime(setup).step("run-1")["status"] == "waiting"
    assert not setup[2].calls and not setup[1].agent3.llm.calls


@pytest.fixture
def api(setup):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import run_supervisor_routes as routes
    from app.orchestration.services import RunSupervisorServices
    app.dependency_overrides[routes.get_services] = lambda: RunSupervisorServices(setup[0], runtime(setup))
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(routes.get_services, None)


def test_api_step_chain_and_no_execution_token_leak(api):
    headers = {"X-Actor-Member-Id": "member-1"}
    for _ in range(6):
        current = api.get("/api/v2/runs/run-1/supervisor", headers=headers).json()
        response = api.post("/api/v2/runs/run-1/supervisor/step", headers=headers,
            json={"expected_state_revision": current["state"]["state_revision"]})
        assert response.status_code == 200, response.text
        assert "execution_token" not in response.text and "executions" not in response.json()
    final = response.json()
    assert final["status"] == "waiting_final_review" and final["documents"]["email"]["body"]
    approved = api.post("/api/v2/runs/run-1/supervisor/final-review", headers=headers,
        json={"expected_state_revision": final["state"]["state_revision"], "operation_id": "pm-approve",
              "bundle": final["review_bundle"], "decision": "approve"})
    assert approved.status_code == 200 and approved.json()["status"] == "completed"


def test_api_requires_actor_and_rejects_actor_spoofing(api):
    assert api.get("/api/v2/runs/run-1/supervisor").status_code == 422
    response = api.post("/api/v2/runs/run-1/supervisor/answers", headers={"X-Actor-Member-Id": "member-1"},
        json={"expected_state_revision": 0, "operation_id": "answer", "question_id": "q1",
              "answer": "예", "answered_by": "other-member"})
    assert response.status_code == 422


def test_http_api_is_disabled_without_backend_contract_support(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.config import settings
    monkeypatch.setattr(settings, "backend_coordination_enabled", False)
    with TestClient(app) as client:
        result = client.get("/api/v2/runs/run-1/supervisor", headers={"X-Actor-Member-Id": "member-1"})
    assert result.status_code == 503 and "BACKEND_COORDINATION_ENABLED" in result.json()["message"]


def test_request_wide_agent1_question_is_not_dropped(setup):
    workers = setup[1]
    original = workers.agent1.run
    def global_question(inp):
        out = original(inp)
        out.questions = [Question(question_id="global", case_id=inp.context.case_id, stage="agent1",
            item_ids=[], field="request_context", text="어느 프로젝트 요청인가요?", reason="프로젝트 확인",
            target="pm", blocking=True)]
        out.status = "NEEDS_INPUT"
        return out
    workers.agent1.run = global_question
    runtime(setup).step("run-1")
    waiting = runtime(setup).step("run-1")
    assert waiting["status"] == "waiting_input"
    assert waiting["run"].coordination.questions[0].item_ids == []
    assert not setup[2].calls


def test_pm_answer_starts_new_segment_but_keeps_total_budget(setup):
    from app.orchestration.run_coordination import PMQuestion, SaveCoordination
    state = setup[0]
    run = state.get_coordinated_run("run-1")
    coord = run.coordination.model_copy(deep=True)
    coord.segment_steps, coord.segment_llm_calls, coord.llm_calls = 11, 30, 55
    coord.questions = [PMQuestion(question_id="q", field="reuse", text="재사용 여부?", item_ids=["CR-A-001-1"])]
    state.save_coordination(SaveCoordination(run_id="run-1", expected_state_revision=0, operation_id="question", state=coord))
    run = state.get_coordinated_run("run-1")
    runtime(setup).answer(AnswerQuestion(run_id="run-1", expected_state_revision=run.state.state_revision,
        operation_id="answer", question_id="q", answer="가능합니다", answered_by="pm"))
    saved = state.get_coordinated_run("run-1").coordination
    assert saved.llm_calls == 55 and saved.segment_llm_calls == 0 and saved.segment_steps == 0


def test_worker_failure_returns_safe_error_and_does_not_leave_running(api, setup):
    from app.agents.base import AgentFailure
    from schema.schemas import AgentError
    def failed(inp):
        raise AgentFailure(AgentError(code="MODEL_TIMEOUT", message="모델 연결 시간이 초과되었습니다", retryable=True))
    setup[1].agent1.run = failed
    result = api.post("/api/v2/runs/run-1/supervisor/step", headers={"X-Actor-Member-Id": "member-1"},
        json={"expected_state_revision": 0})
    assert result.status_code == 502 and result.json()["code"] == "MODEL_TIMEOUT"
    assert setup[0].get_state("run-1").agents["agent1"].status == "FAILED"
    assert setup[0].get_coordinated_run("run-1").executions == {}


def test_auto_execute_api_runs_until_final_review_without_continue_clicks(api):
    response = api.post("/api/v2/runs/run-1/supervisor/execute", headers={"X-Actor-Member-Id": "member-1"},
                        json={"expected_state_revision": 0})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "waiting_final_review"
    assert response.json()["documents"]["email"]["body"]


def test_auto_execution_stops_at_pm_question_without_agent3(setup):
    original = setup[1].agent2.run
    def scope_question(inp, focus=None):
        out = original(inp, focus=focus)
        out.questions = [Question(question_id="q", case_id=inp.context.case_id, stage="agent2", item_ids=["CR-A-001-1"],
            field="reuse", text="기존 기능 재사용 가능 여부를 확인해 주세요", reason="담당자 확인", target="pm", blocking=False)]
        return out
    setup[1].agent2.run = scope_question
    result = runtime(setup).run("run-1", expected_revision=0)
    assert result["status"] == "waiting_input"
    assert [e["action"] for e in result["events"]] == ["analyze_request", "assess_scope"]
    assert not setup[1].agent3.llm.calls


def test_pm_answer_after_impact_researches_reestimates_and_drafts_same_run(setup):
    from app.agents.agent3.contracts import Agent3Need
    state, workers, decisions, draft = setup
    original = workers.agent3.run
    inputs = []
    def need_once(inp):
        inputs.append(inp.model_copy(deep=True))
        out = original(inp)
        if len(inputs) == 1:
            out.status = "NEEDS_INPUT"
            out.needs = [Agent3Need(kind="client_input", item_ids=["CR-A-001-1"], topic="사진 업로드 재사용 가능 여부")]
        return out
    workers.agent3.run = need_once
    first = runtime(setup).run("run-1", expected_revision=0)
    assert first["status"] == "waiting_input"
    waiting = first["run"]
    qid = waiting.coordination.questions[0].question_id
    runtime(setup).answer(AnswerQuestion(run_id="run-1", expected_state_revision=waiting.state.state_revision,
        operation_id="reuse-answer", question_id=qid, answer="기존 사진 업로드를 재사용할 수 있습니다", answered_by="pm"))
    followup = FakeLlm([
        {"action": "assess_scope", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "답변을 근거 검토에 반영"},
        {"action": "estimate_impact", "args": {"item_ids": ["CR-A-001-1"]}, "reason": "가정 갱신"},
        {"action": "draft_documents", "args": {"item_ids": ["CR-A-001-1", "CR-A-001-3"], "kind": "negotiation_email"}, "reason": "초안"},
    ])
    resumed = RunSupervisorRuntime(state, workers, followup, draft).run("run-1")
    assert resumed["status"] == "waiting_final_review"
    assert len(inputs) == 2 and inputs[-1].answers[0].question_id == qid
    assert resumed["run"].state.run_id == "run-1"
    assert resumed["run"].state.agents["agent3"].result_revision == 3  # initial impact, re-estimate, documents
    assert len(workers.agent2.calls) == 2 and len(draft.calls) == 1


def test_optional_agent3_suggestion_does_not_restart_pm_round(setup):
    original = setup[1].agent3.run
    def with_suggestion(inp):
        out = original(inp)
        out.questions.append(Question(question_id="optional", case_id=inp.context.case_id, stage="agent3",
            item_ids=["CR-A-001-1"], text="평균 별점도 필요할 수 있습니다", field="extra_display", target="pm",
            reason="추가 제안", blocking=False))
        return out
    setup[1].agent3.run = with_suggestion
    result = runtime(setup).run("run-1")
    assert result["status"] == "waiting_final_review"
    assert result["run"].coordination.questions == []
    assert len(setup[1].agent3.llm.calls) == 1
