"""Agent 2 오케스트레이터 도구 어댑터: ID 인자, 같은 작업 재전송, 바뀐 항목만 재판단, 상태 충돌, 오류 분류."""
from schema.schemas import Agent2Input, Answer, Context, Question
from test_agent2 import GOLD, INPUTS, GoldLLM

from app.agents.agent2.agent import LlmAgent2
from app.agents.agent2.orchestrator_adapter import (
    Agent2CaseSnapshot, Agent2OrchestratorAdapter, action_schema, tool_schemas,
)
from app.agents.agent3.workflow import WorkflowConflict
from app.tools.errors import BaselineSuperseded


class MemoryPort:
    def __init__(self, snapshot):
        self.snapshot, self.receipts, self.updates = snapshot, {}, []

    def load(self, case_id):
        return self.snapshot.model_copy(deep=True)

    def read_operation(self, case_id, operation_id):
        return self.receipts.get(operation_id)

    def commit(self, case_id, expected_revision, update, operation_id, receipt):
        if self.snapshot.state_revision != expected_revision:
            raise WorkflowConflict("stale")
        if update is not None:
            self.snapshot = self.snapshot.model_copy(update={
                "state_revision": expected_revision + 1, "a2_result": update.a2_result,
                "a2_item_hashes": update.a2_item_hashes})
            self.updates.append(update)
        self.receipts[operation_id] = receipt


class CountingAgent:
    def __init__(self, case_id, fail=None):
        self.agent, self.calls, self.fail = LlmAgent2(GoldLLM(GOLD[case_id])), [], fail

    def run(self, inp: Agent2Input, focus=None):
        self.calls.append(([i.item_id for i in inp.items], focus))
        if self.fail:
            raise self.fail
        return self.agent.run(inp, focus=focus)


def _setup(case_id, **kw):
    raw = INPUTS[case_id]
    snap = Agent2CaseSnapshot(state_revision=0, context=Context.model_validate(raw["context"]),
                              message_text=raw["message_text"], a1_result_id=raw.get("a1_result_id"),
                              items=raw["items"], constraints=raw.get("constraints", []))
    port, agent = MemoryPort(snap), CountingAgent(case_id, **kw)
    return port, agent, Agent2OrchestratorAdapter(port, lambda s: agent)


def _call(item_ids, focus=None):
    return {"action": "assess_scope", "args": {"item_ids": item_ids, **({"focus": focus} if focus else {})},
            "reason": "범위 판단"}


def _ids(case_id):
    return [i["item_id"] for i in INPUTS[case_id]["items"]]


def test_schemas_expose_only_ids_and_focus():
    params = tool_schemas()[0]["parameters"]
    assert set(params["properties"]) == {"item_ids", "focus"} and params["additionalProperties"] is False
    assert action_schema()["properties"]["action"]["const"] == "assess_scope"


def test_first_call_judges_and_commits_summary():
    port, agent, adapter = _setup("CR-A-004")
    r = adapter.execute("CR-A-004", 0, "op-1", _call(_ids("CR-A-004")))
    assert r.status == "ready" and r.processed_item_ids == _ids("CR-A-004")
    assert [i.label for i in r.items] == [a["label"] for a in GOLD["CR-A-004"]["assessments"]]
    assert r.result_ids == [port.snapshot.a2_result.meta.result_id]
    assert port.snapshot.state_revision == 1 and len(agent.calls) == 1


def test_open_question_becomes_need_with_question_id():
    port, agent, adapter = _setup("CR-A-005")                        # issue_report — 고객에게 재현 조건 질문
    r = adapter.execute("CR-A-005", 0, "op-1", _call(_ids("CR-A-005")))
    q = port.snapshot.a2_result.questions[0]
    assert any(n.kind == "client_input" and n.question_id == q.question_id and n.target == q.target for n in r.needs)
    assert all(not i.estimable for i in r.items)


def test_same_operation_is_replayed_and_conflicting_args_rejected():
    port, agent, adapter = _setup("CR-A-001")
    first = adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001")))
    again = adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001")))
    assert again == first and len(agent.calls) == 1
    other = adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001"), focus="변경합의서"))
    assert other.status == "error" and other.error.code == "OPERATION_CONFLICT"


def test_unchanged_input_reuses_judgment_without_model_call():
    port, agent, adapter = _setup("CR-A-001")
    adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001")))
    r = adapter.execute("CR-A-001", 1, "op-2", _call(_ids("CR-A-001")))
    assert r.status == "ready" and r.processed_item_ids == [] and len(agent.calls) == 1
    assert len(port.updates) == 1                                     # 영수증만 저장


def test_focus_or_new_answer_triggers_rejudgment():
    port, agent, adapter = _setup("CR-A-001")
    adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001")))
    adapter.execute("CR-A-001", 1, "op-2", _call(_ids("CR-A-001"), focus="변경합의서의 사진 개수 조건"))
    assert agent.calls[-1][1] == "변경합의서의 사진 개수 조건"
    port.snapshot = port.snapshot.model_copy(update={"answers": [Answer(answer_id="A1", question_id="Q", answered_by="pm", text="사진 3장",
                                                                           answered_at="2026-11-05T10:00:00+09:00")]})
    r = adapter.execute("CR-A-001", 2, "op-3", _call(_ids("CR-A-001")))
    assert r.processed_item_ids == _ids("CR-A-001") and len(agent.calls) == 3
    assert port.snapshot.a2_result.meta.result_revision == 3


def test_partial_rejudgment_keeps_other_items():
    port, agent, adapter = _setup("CR-A-004")
    first, second = _ids("CR-A-004")
    adapter.execute("CR-A-004", 0, "op-1", _call([first, second]))
    before = {a.item_id: a for a in port.snapshot.a2_result.assessments}
    r = adapter.execute("CR-A-004", 1, "op-2", _call([first], focus="계약서 일정 조항"))
    assert agent.calls[-1][0] == [first] and r.requested_item_ids == [first]
    after = {a.item_id: a for a in port.snapshot.a2_result.assessments}
    assert after[second] == before[second] and list(after) == [first, second]
    ids = [q.question_id for q in port.snapshot.a2_result.questions]
    assert len(ids) == len(set(ids))


def test_stale_revision_and_bad_args_are_rejected():
    port, agent, adapter = _setup("CR-A-001")
    assert adapter.execute("CR-A-001", 5, "op-1", _call(_ids("CR-A-001"))).error.code == "STATE_CONFLICT"
    assert adapter.execute("CR-A-001", 0, "op-2", _call(["없는-항목"])).error.code == "INVALID_ARGUMENT"
    sneaky = {**_call(_ids("CR-A-001")), "args": {"item_ids": _ids("CR-A-001"), "baseline_id": "P-COM-101:BL-1"}}
    assert adapter.execute("CR-A-001", 0, "op-3", sneaky).error.code == "INVALID_ARGUMENT"
    assert adapter.execute("CR-A-001", 0, "", _call(_ids("CR-A-001"))).error.code == "INVALID_ARGUMENT"
    assert agent.calls == []


def test_non_scope_intent_is_not_judged():
    port, agent, adapter = _setup("CR-A-006")                        # status_check
    r = adapter.execute("CR-A-006", 0, "op-1", _call(_ids("CR-A-006")))
    assert r.error.code == "INVALID_ARGUMENT" and agent.calls == []


def test_superseded_baseline_is_not_retryable():
    port, agent, adapter = _setup("CR-A-001", fail=BaselineSuperseded("BASELINE_SUPERSEDED: x"))
    r = adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001")))
    assert r.error.code == "BASELINE_SUPERSEDED" and r.error.retryable is False and port.updates == []


def test_split_question_routes_back_to_agent1():
    from app.agents.agent2.orchestrator_adapter import ACTION
    port, agent, adapter = _setup("CR-A-001")
    adapter.execute("CR-A-001", 0, "op-1", _call(_ids("CR-A-001")))
    out = port.snapshot.a2_result
    q = {"question_id": "CR-A-001-A2Q1", "case_id": "CR-A-001", "stage": "agent2", "item_ids": _ids("CR-A-001"),
         "field": "item_split", "text": "항목을 나눠 주세요", "reason": "포함·제외 기능 혼재", "target": "pm", "blocking": True}
    out = out.model_copy(update={"status": "NEEDS_INPUT", "questions": [Question.model_validate(q)]})
    r = Agent2OrchestratorAdapter._result("op-2", ACTION.validate_python(_call(_ids("CR-A-001"))), port.snapshot, out, [])
    assert r.status == "needs_input" and r.needs[0].kind == "resplit" and r.needs[0].target == "agent1"
    assert r.items[0].estimable is False


def _confirmation_result(followup):
    from app.agents.agent2.orchestrator_adapter import ACTION
    port, agent, adapter = _setup("CR-A-002")                        # confirmation 항목
    adapter.execute("CR-A-002", 0, "op-1", _call(_ids("CR-A-002")))
    out = port.snapshot.a2_result
    a = out.assessments[0].model_copy(update={"label": "out_of_scope"})
    for e in a.evidence:
        e.verified = True
    snap = port.snapshot.model_copy(update={"items": [port.snapshot.items[0].model_copy(update={"conditional_followup": followup})]})
    return Agent2OrchestratorAdapter._result("op-2", ACTION.validate_python(_call(_ids("CR-A-002"))), snap,
                                             out.model_copy(update={"assessments": [a]}), [])


def test_plain_confirmation_is_not_estimable_even_if_out_of_scope():
    r = _confirmation_result(None)
    assert r.items[0].label == "out_of_scope" and r.items[0].estimable is False
    r = _confirmation_result("범위 밖이면 추가 희망")
    assert r.items[0].estimable is True


def test_agent3_evidence_need_can_be_passed_to_agent2_adapter_without_direct_agent_call():
    from app.agents.agent3.contracts import Agent3Need
    from app.orchestration.evidence_handoff import propose_scope_research
    port, agent, adapter = _setup("CR-A-001")
    ids = _ids("CR-A-001")
    need = Agent3Need(kind="more_evidence", item_ids=ids, topic="변경합의서 범위 확인", source="agent3")
    proposed = propose_scope_research(need, set(ids))
    result = adapter.execute("CR-A-001", 0, "research-1", proposed.model_dump())
    assert agent.calls == [(ids, "변경합의서 범위 확인")]
    assert result.status in ("ready", "needs_input")
    assert port.snapshot.a2_result is not None
