"""ID-only Agent 2 tool for the orchestrator. A trusted host injects state; no orchestrator loop lives here.

Agent 3 어댑터(`app/agents/agent3/orchestrator_adapter.py`)와 같은 형식을 쓴다:
오케스트레이터 에이전트는 {action, args, reason}만 고르고, 기준선·원문·답변·모델은 호스트 상태에서 채운다.
Agent 2는 항목별로 독립 판단하므로 세션이 없다. 바뀐 항목만 다시 판단해 이전 결과에 합친다.
"""
from collections.abc import Callable
from threading import RLock
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from schema.schemas import Agent2Input, Agent2Output, Answer, Context, Item, Question
from app.agents.agent3.workflow import WorkflowConflict, _hash
from app.agents.base import AgentFailure
from app.tools.errors import ToolInputError

VERSION = "a2-orchestration/1.0.0-draft"
SCOPE_INTENTS = {"change_request", "confirmation", "defect", "issue_report"}   # engine.py와 같은 대상
SPLIT_FIELD = "item_split"                                                       # 프롬프트 규칙 5의 항목 분리 질문


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AssessArgs(StrictModel):
    item_ids: list[str] = Field(min_length=1)
    focus: Optional[str] = Field(None, min_length=1, max_length=200,
                                 description="추가 조사 관점. 있으면 결과가 같아도 다시 조사한다")

    @field_validator("item_ids")
    @classmethod
    def distinct_ids(cls, ids):
        if len(ids) != len(set(ids)) or any(not i.strip() for i in ids):
            raise ValueError("item IDs must be nonempty and unique")
        return ids


class AssessAction(StrictModel):
    action: Literal["assess_scope"]
    args: AssessArgs
    reason: str = Field(min_length=1, max_length=300)


ACTION = TypeAdapter(AssessAction)


def tool_schemas():
    """AI가 호출할 수 있는 기능만. 기준선·모델·답변은 인자로 받지 않는다."""
    return [{"name": "assess_scope", "description": "항목의 계약 범위 판단·범위 확인 문의 답변·추가 조사",
             "parameters": AssessArgs.model_json_schema()}]


def action_schema():
    return ACTION.json_schema()


class Agent2CaseSnapshot(StrictModel):
    """신뢰된 호스트 상태. LLM 도구 인자에 포함하지 않는다."""
    state_revision: int = Field(ge=0)
    context: Context
    message_text: str
    a1_result_id: Optional[str] = None
    items: list[Item]
    constraints: list[Item] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)
    execution_config_revision: str = "default"
    a2_result: Optional[Agent2Output] = None
    a2_item_hashes: dict[str, str] = Field(default_factory=dict, description="item_id → 판단에 쓴 입력 fingerprint")

    def item_hash(self, item: Item) -> str:
        # 답변·제약·원문·기준선 중 하나라도 바뀌면 다시 판단한다 (답변이 어떤 항목에 영향을 주는지 Agent 2가 알 수 없어 전체 비교)
        return _hash({"item": item.model_dump(mode="json"), "context": self.context.model_dump(mode="json"),
                      "message": self.message_text, "constraints": [c.model_dump(mode="json") for c in self.constraints],
                      "answers": [a.model_dump(mode="json") for a in self.answers], "a1": self.a1_result_id,
                      "config": self.execution_config_revision})


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False
    stage: str = "agent2"


class Agent2Need(StrictModel):
    need_id: str
    kind: Literal["client_input", "more_evidence", "resplit"]
    item_ids: list[str] = Field(min_length=1)
    topic: str
    target: Literal["pm", "client", "agent1", "agent2"]
    source: Literal["agent2"] = "agent2"
    question_id: Optional[str] = None
    blocking: bool


class ItemSummary(StrictModel):
    """오케스트레이터 에이전트에게 주는 판단 요약. 근거 전문은 넣지 않는다."""
    item_id: str
    intent: str
    label: str
    evidence_level: Optional[str]
    verified_evidence: int
    approved_change_ref: Optional[str] = None
    confirmation_answer: Optional[str] = None
    conditional_followup: bool = False
    estimable: bool = Field(description="잠정 산정 조건(범위 밖 + 검증 근거 + 블로킹 질문 없음, 확인 문의는 조건부 후속 요청 필요) 충족")


class ToolResult(StrictModel):
    contract_version: str = VERSION
    operation_id: str
    status: Literal["ready", "needs_input", "error"]
    requested_item_ids: list[str] = Field(default_factory=list)
    processed_item_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    summary: str
    items: list[ItemSummary] = Field(default_factory=list)
    needs: list[Agent2Need] = Field(default_factory=list)
    error: Optional[ToolError] = None


class OperationReceipt(StrictModel):
    request_hash: str
    result: ToolResult


class Agent2Update(StrictModel):
    a2_result: Agent2Output
    a2_item_hashes: dict[str, str]
    source_result_ids: dict[str, str]
    active_questions: list[Question]
    resolved_question_ids: list[str] = Field(default_factory=list)
    superseded_question_ids: list[str] = Field(default_factory=list)


class Agent2StatePort(Protocol):
    """Agent3StatePort와 같은 약속: load는 분리된 스냅샷, commit은 revision 비교·반영·영수증 저장을 원자적으로."""
    def load(self, case_id: str) -> Agent2CaseSnapshot: ...
    def read_operation(self, case_id: str, operation_id: str) -> Optional[OperationReceipt]: ...
    def commit(self, case_id: str, expected_revision: int, update: Optional[Agent2Update],
               operation_id: str, receipt: OperationReceipt) -> None: ...


class AdapterError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Agent2Runner(Protocol):
    def run(self, inp: Agent2Input, focus: Optional[str] = None) -> Agent2Output: ...


class Agent2OrchestratorAdapter:
    def __init__(self, state: Agent2StatePort, agent_factory: Callable[[Agent2CaseSnapshot], Agent2Runner]):
        self.state, self.agent_factory = state, agent_factory
        self._locks = {}
        self._lock = RLock()

    def _case_lock(self, case_id):
        with self._lock:
            return self._locks.setdefault(case_id, RLock())

    def execute(self, case_id, expected_state_revision, operation_id, call):
        """앞의 세 인자는 호스트가 주입한다. call만 오케스트레이터 에이전트의 결정이다."""
        with self._case_lock(case_id):
            request_hash = None
            try:
                if not operation_id or not operation_id.strip():
                    raise AdapterError("INVALID_ARGUMENT", "서버 작업 ID가 필요합니다.")
                action = ACTION.validate_python(call)
                request_hash = _hash({"revision": expected_state_revision, "call": action.model_dump()})
                previous = self.state.read_operation(case_id, operation_id)
                if previous:
                    if previous.request_hash != request_hash:
                        raise AdapterError("OPERATION_CONFLICT", "같은 작업 ID에 다른 인자를 사용할 수 없습니다.")
                    return previous.result.model_copy(deep=True)
                snapshot = self.state.load(case_id)
                if snapshot.state_revision != expected_state_revision or snapshot.context.case_id != case_id:
                    raise WorkflowConflict("stale host state or case mismatch")
                result, update = self._assess(snapshot, action, operation_id)
                current = self.state.load(case_id)          # 실행 중 입력이 바뀌었으면 늦은 결과로 덮어쓰지 않는다
                if current.state_revision != expected_state_revision or _hash(current.model_dump(mode="json")) != _hash(snapshot.model_dump(mode="json")):
                    raise WorkflowConflict("host input changed during execution")
                self.state.commit(case_id, expected_state_revision, update, operation_id,
                                  OperationReceipt(request_hash=request_hash, result=result))
                return result
            except Exception as error:
                if request_hash and isinstance(error, WorkflowConflict):
                    receipt = self.state.read_operation(case_id, operation_id)
                    if receipt and receipt.request_hash == request_hash:
                        return receipt.result.model_copy(deep=True)
                return self._error(operation_id, error)

    def _assess(self, snapshot: Agent2CaseSnapshot, action: AssessAction, operation_id: str):
        by_id = {i.item_id: i for i in snapshot.items}
        selected = action.args.item_ids
        if not set(selected) <= set(by_id):
            raise AdapterError("INVALID_ARGUMENT", "현재 요청에 없는 항목입니다.")
        if any(by_id[i].intent not in SCOPE_INTENTS for i in selected):
            raise AdapterError("INVALID_ARGUMENT", "범위 판단 대상이 아닌 항목입니다 (intent 확인).")
        hashes = {i: snapshot.item_hash(by_id[i]) for i in selected}
        prev = snapshot.a2_result
        done = {a.item_id for a in prev.assessments} if prev else set()
        stale = [i for i in selected if action.args.focus or i not in done or snapshot.a2_item_hashes.get(i) != hashes[i]]
        if not stale:                                    # 같은 입력의 판단이 이미 있다 — 모델을 다시 부르지 않는다
            return self._result(operation_id, action, snapshot, prev, []), None
        inp = Agent2Input(context=snapshot.context, message_text=snapshot.message_text, a1_result_id=snapshot.a1_result_id,
                          items=[by_id[i] for i in stale], constraints=snapshot.constraints, answers=snapshot.answers)
        fresh = self.agent_factory(snapshot).run(inp, focus=action.args.focus)
        merged, resolved, superseded = self._merge(snapshot, prev, fresh, stale, operation_id)
        update = Agent2Update(
            a2_result=merged, a2_item_hashes={**snapshot.a2_item_hashes, **{i: hashes[i] for i in stale}},
            source_result_ids={k: v for k, v in {"agent1": snapshot.a1_result_id, "agent2": merged.meta.result_id}.items() if v},
            active_questions=list(merged.questions), resolved_question_ids=resolved, superseded_question_ids=superseded)
        return self._result(operation_id, action, snapshot, merged, stale), update

    @staticmethod
    def _merge(snapshot, prev: Optional[Agent2Output], fresh: Agent2Output, processed: list[str], operation_id: str):
        """다시 판단한 항목만 바꾸고 나머지 항목의 판단·질문은 그대로 둔다. 질문 ID는 이전 것과 겹치지 않게 다시 붙인다."""
        case_id, done = snapshot.context.case_id, set(processed)
        kept_a = [a for a in (prev.assessments if prev else []) if a.item_id not in done]
        kept_q = [q for q in (prev.questions if prev else []) if not set(q.item_ids) <= done]
        dropped = [q for q in (prev.questions if prev else []) if set(q.item_ids) <= done]
        used = {q.question_id for q in kept_q}
        new_q, n = [], len(prev.questions) if prev else 0
        for q in fresh.questions:
            n += 1
            while f"{case_id}-A2Q{n}" in used:
                n += 1
            new_q.append(q.model_copy(update={"question_id": f"{case_id}-A2Q{n}"}))
        # 같은 항목에 같은 질문이 다시 나오면 이전 질문이 아직 열린 것 — 해소되지 않음(superseded)으로 구분한다
        still = {(tuple(q.item_ids), q.field) for q in new_q}
        resolved = [q.question_id for q in dropped if (tuple(q.item_ids), q.field) not in still]
        superseded = [q.question_id for q in dropped if (tuple(q.item_ids), q.field) in still]
        order = {i.item_id: n for n, i in enumerate(snapshot.items)}
        assessments = sorted(kept_a + list(fresh.assessments), key=lambda a: order.get(a.item_id, len(order)))
        questions = kept_q + new_q
        status = "NEEDS_INPUT" if any(q.blocking and q.status == "open" for q in questions) else "READY_FOR_REVIEW"
        meta = fresh.meta.model_copy(update={
            "result_id": f"a2r-{_hash({'case': case_id, 'operation': operation_id})[:24]}",
            "result_revision": (prev.meta.result_revision + 1) if prev else 1,
            "upstream_result_ids": [x for x in [snapshot.a1_result_id] if x],
            "debug": {**fresh.meta.debug, "processed_item_ids": processed,
                      "previous_result_id": prev.meta.result_id if prev else None}})
        merged = Agent2Output(context=snapshot.context, status=status, assessments=assessments, questions=questions, meta=meta)
        return merged, resolved, superseded

    @staticmethod
    def _result(operation_id, action, snapshot, out: Agent2Output, processed: list[str]) -> ToolResult:
        by_id = {i.item_id: i for i in snapshot.items}
        requested = action.args.item_ids
        assessed = {a.item_id: a for a in out.assessments}
        open_q = [q for q in out.questions if q.status == "open" and set(q.item_ids) & set(requested)]
        blocked_items = {i for q in open_q if q.blocking for i in q.item_ids}
        needs, asked = [], {i for q in open_q for i in q.item_ids}
        for q in open_q:
            split = q.field == SPLIT_FIELD
            needs.append(Agent2Need(need_id=f"a2-need-{_hash({'q': q.question_id, 'r': out.meta.result_id})[:24]}",
                                    kind="resplit" if split else "client_input", item_ids=q.item_ids, topic=q.text,
                                    target="agent1" if split else q.target, question_id=q.question_id, blocking=q.blocking))
        for i in requested:
            a = assessed[i]
            if a.label == "unclear" and i not in asked:   # 물을 것도 없이 근거가 부족하다 — 다른 관점(focus)으로 다시 조사할 수 있다
                needs.append(Agent2Need(need_id=f"a2-need-{_hash({'i': i, 'r': out.meta.result_id})[:24]}",
                                        kind="more_evidence", item_ids=[i], topic="; ".join(a.missing_info) or "근거 부족",
                                        target="agent2", blocking=False))
        items = [ItemSummary(item_id=i, intent=by_id[i].intent, label=assessed[i].label,
                             evidence_level=assessed[i].evidence_level,
                             verified_evidence=sum(1 for e in assessed[i].evidence if e.verified),
                             approved_change_ref=assessed[i].approved_change_ref,
                             confirmation_answer=assessed[i].confirmation_answer,
                             conditional_followup=bool(by_id[i].conditional_followup),
                             estimable=assessed[i].label == "out_of_scope" and any(e.verified for e in assessed[i].evidence)
                             and i not in blocked_items
                             # 묻기만 한 확인 문의는 범위 밖이어도 견적 대상이 아니다 — 원문의 조건부 추가 요청이 있을 때만 (Agent 1 계약)
                             and (by_id[i].intent != "confirmation" or bool(by_id[i].conditional_followup)))
                 for i in requested]
        blocked = any(n.blocking for n in needs)
        return ToolResult(operation_id=operation_id, status="needs_input" if blocked else "ready",
                          requested_item_ids=requested, processed_item_ids=processed, result_ids=[out.meta.result_id],
                          summary="추가 확인이 필요합니다." if blocked else "요청한 범위 판단을 완료했습니다.",
                          items=items, needs=needs)

    @staticmethod
    def _error(operation_id, error):
        if isinstance(error, AdapterError):
            detail = ToolError(code=error.code, message=str(error))
        elif isinstance(error, WorkflowConflict):
            detail = ToolError(code="STATE_CONFLICT", message="실행 상태가 변경됐습니다. 최신 상태를 확인해 주세요.")
        elif isinstance(error, ToolInputError):               # BASELINE_SUPERSEDED 포함 — 재시도해도 같다
            detail = ToolError(code=error.code, message=str(error))
        elif isinstance(error, AgentFailure):
            detail = ToolError(code=error.error.code, message=error.error.message,
                               retryable=error.error.retryable or error.error.code == "OUTPUT_INVALID")
        elif isinstance(error, (ValidationError, ValueError)):
            detail = ToolError(code="INVALID_ARGUMENT", message="도구 인자 또는 입력 조건을 확인해 주세요.")
        else:
            detail = ToolError(code="INTERNAL_ERROR", message="Agent 2 연결 계층에서 처리에 실패했습니다.")
        return ToolResult(operation_id=operation_id or "", status="error", summary=detail.message, error=detail)
