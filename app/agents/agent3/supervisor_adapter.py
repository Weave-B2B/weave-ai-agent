"""ID-only Agent 3 tools. A trusted host injects state; no supervisor loop lives here.

The host implements Agent3StatePort (atomic revision check + update + receipt).
Agent 3 transactions are forked and published only after that commit succeeds.
Tool sessions remain in memory; durable recovery belongs to the host integration.
"""
from collections.abc import Callable
from threading import RLock
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from schema.schemas import Agent1Input, Agent2Output, Answer, Item, Question
from app.agents.base import AgentFailure
from app.agents.agent3.contracts import ProvisionalAgent3Input
from app.agents.agent3.orchestration import (
    Agent3Orchestration, DraftDocuments, Reestimate, Recalculate, FinalReview,
    EditDocument, FullReestimationRequired, WorkEdit,
)
from app.agents.agent3.workflow import WorkflowConflict, _hash

VERSION = "a3-orchestration/1.0.0-draft"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ItemArgs(StrictModel):
    item_ids: list[str] = Field(min_length=1)

    @field_validator("item_ids")
    @classmethod
    def distinct_ids(cls, ids):
        if len(ids) != len(set(ids)) or any(not i.strip() for i in ids):
            raise ValueError("item IDs must be nonempty and unique")
        return ids


class DocumentArgs(ItemArgs):
    kind: Literal["negotiation_email", "change_agreement", "scope_response"]


class EstimateAction(StrictModel):
    action: Literal["estimate_impact"]
    args: ItemArgs
    reason: str = Field(min_length=1, max_length=300)


class DocumentAction(StrictModel):
    action: Literal["draft_documents"]
    args: DocumentArgs
    reason: str = Field(min_length=1, max_length=300)


SupervisorAction = Annotated[EstimateAction | DocumentAction, Field(discriminator="action")]
ACTION = TypeAdapter(SupervisorAction)


def tool_schemas():
    """Only AI-callable capabilities. PM writes and runtime arguments are excluded."""
    return [
        {"name": "estimate_impact", "description": "항목의 잠정 영향 산정 또는 재산정", "parameters": ItemArgs.model_json_schema()},
        {"name": "draft_documents", "description": "최신 결과로 고객 문서 초안 생성", "parameters": DocumentArgs.model_json_schema()},
    ]


def action_schema():
    """Full {action,args,reason} schema for a structured supervisor decision."""
    return ACTION.json_schema()


class Agent3Reference(StrictModel):
    session_id: str
    state_revision: int
    snapshot_hash: str
    input_hash: str
    execution_config_revision: str


class Agent3CaseSnapshot(StrictModel):
    """Trusted host state, never part of the LLM function arguments."""
    state_revision: int = Field(ge=0)
    a1_input: Agent1Input
    a1_result_id: str = Field(min_length=1)
    items: list[Item]
    a2_result: Agent2Output
    rate_card_id: str
    constraints: list[Item] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)
    conditional_item_ids: list[str] = Field(default_factory=list)
    quote_links: dict[str, list[str]] = Field(default_factory=dict)
    customer_questions: list[str] = Field(default_factory=list)
    execution_config_revision: str = "default"
    a3_ref: Agent3Reference | None = None

    def input(self):
        if any(i.intent == "estimate_only" for i in self.items):
            raise AdapterError("INVALID_ARGUMENT", "estimate_only의 의미를 먼저 확정해야 합니다.")
        required = {i.item_id for i in self.items if i.intent in ("change_request", "estimate_request")}
        required = (required - set(self.quote_links)) | set(self.conditional_item_ids)
        required.update(i for ids in self.quote_links.values() for i in ids)
        return ProvisionalAgent3Input(context=self.a1_input.context.model_copy(deep=True),
            received_at=self.a1_input.received_at, rate_card_id=self.rate_card_id,
            items=self.items, constraints=self.constraints, a2_result=self.a2_result,
            answers=self.answers, conditional_item_ids=self.conditional_item_ids,
            quote_links=self.quote_links, customer_questions=self.customer_questions,
            target_item_ids=sorted(required))


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False
    stage: str = "agent3"


class ToolResult(StrictModel):
    contract_version: str = VERSION
    operation_id: str
    status: Literal["ready", "needs_input", "not_required", "error"]
    requested_item_ids: list[str] = Field(default_factory=list)
    processed_item_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    summary: str
    needs: list[dict] = Field(default_factory=list)
    error: ToolError | None = None


class OperationReceipt(StrictModel):
    request_hash: str
    result: ToolResult


class Agent3Update(StrictModel):
    a3_ref: Agent3Reference
    view: dict
    rate_card_id: str
    source_result_ids: dict[str, str]
    active_questions: list[Question]
    resolved_question_ids: list[str] = Field(default_factory=list)
    superseded_question_ids: list[str] = Field(default_factory=list)


class Agent3StatePort(Protocol):
    """Return detached snapshots. Commit must atomically CAS + store update/receipt.

    A None update stores only the receipt (cached read). A stale revision must
    raise WorkflowConflict. No implementation for the shared engine is assumed.
    """
    def load(self, case_id: str) -> Agent3CaseSnapshot: ...
    def read_operation(self, case_id: str, operation_id: str) -> OperationReceipt | None: ...
    def commit(self, case_id: str, expected_revision: int, update: Agent3Update | None,
               operation_id: str, receipt: OperationReceipt) -> None: ...


class AdapterError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class PmCalculationArgs(StrictModel):
    edits: list[WorkEdit] = Field(default_factory=list)
    rate_card_id: str | None = None
    reason: str = Field(min_length=1)


class PmRecalculate(StrictModel):
    action: Literal["recalculate"]
    args: PmCalculationArgs


class PmEdit(StrictModel):
    action: Literal["edit_document"]
    kind: Literal["negotiation_email", "change_agreement", "scope_response"]
    narrative: str
    reason: str


class PmReview(StrictModel):
    action: Literal["review"]
    expected_snapshot_hash: str
    decision: Literal["approve", "hold", "reject"]
    reason: str = ""
    acknowledge_unknown_schedule: bool = False


PM_ACTION = TypeAdapter(Annotated[PmRecalculate | PmEdit | PmReview, Field(discriminator="action")])
DOCUMENT_KEYS = {"negotiation_email": "email", "change_agreement": "agreement", "scope_response": "email"}


class Agent3SupervisorAdapter:
    def __init__(self, state: Agent3StatePort, tools_factory: Callable[[Agent3CaseSnapshot], Agent3Orchestration]):
        self.state, self.tools_factory = state, tools_factory
        self._services = {}
        self._locks = {}
        self._lock = RLock()

    def _case_lock(self, case_id):
        with self._lock:
            return self._locks.setdefault(case_id, RLock())

    def execute(self, case_id, expected_state_revision, operation_id, call):
        """The first three arguments are injected by the host, not the LLM."""
        return self._execute(case_id, expected_state_revision, operation_id, call, actor_ref=None)

    def execute_pm(self, case_id, expected_state_revision, operation_id, command, *, actor_ref):
        """Trusted, authenticated host path. Never included in tool_schemas()."""
        if not actor_ref or not actor_ref.strip():
            return self._error(operation_id, AdapterError("INVALID_ARGUMENT", "PM 인증 정보가 필요합니다."))
        return self._execute(case_id, expected_state_revision, operation_id, command, actor_ref=actor_ref.strip())

    def _execute(self, case_id, revision, operation_id, raw, actor_ref):
        with self._case_lock(case_id):
            request_hash = None
            try:
                if not operation_id or not operation_id.strip():
                    raise AdapterError("INVALID_ARGUMENT", "서버 작업 ID가 필요합니다.")
                action = (PM_ACTION if actor_ref is not None else ACTION).validate_python(raw)
                request_hash = _hash({"revision": revision, "call": action.model_dump(), "actor": actor_ref})
                previous = self.state.read_operation(case_id, operation_id)
                if previous:
                    if previous.request_hash != request_hash:
                        raise AdapterError("OPERATION_CONFLICT", "같은 작업 ID에 다른 인자를 사용할 수 없습니다.")
                    return previous.result.model_copy(deep=True)
                snapshot = self.state.load(case_id)
                if snapshot.state_revision != revision or snapshot.a1_input.context.case_id != case_id:
                    raise WorkflowConflict("stale host state or case mismatch")
                inp = snapshot.input()
                key = (inp.context.project_id, case_id)
                old_service = self._services.get(key)
                config_changed = snapshot.a3_ref and snapshot.a3_ref.execution_config_revision != snapshot.execution_config_revision
                candidate = old_service.fork() if old_service and not config_changed else self.tools_factory(snapshot).fork()
                old_view = None
                if snapshot.a3_ref and old_service and not config_changed:
                    old_view = candidate.get(snapshot.a3_ref.session_id)
                    if old_view["state_revision"] != snapshot.a3_ref.state_revision or old_view["snapshot_hash"] != snapshot.a3_ref.snapshot_hash:
                        raise WorkflowConflict("Agent 3 reference changed")
                view, processed, changed = self._dispatch(candidate, snapshot, inp, old_view, action, actor_ref, operation_id)
                if view["phase"] == "STALE":
                    raise WorkflowConflict("stale Agent 3 result")
                result = self._result(operation_id, action, inp, view, processed)
                actual_rate = candidate.source_input(view["session_id"]).rate_card_id
                update = self._update(snapshot, inp.model_copy(update={"rate_card_id": actual_rate}), view, old_view, processed) if changed else None
                # Re-read as well as CAS: the port must version every upstream mutation.
                current = self.state.load(case_id)
                if current.state_revision != revision or _hash(current.model_dump()) != _hash(snapshot.model_dump()):
                    raise WorkflowConflict("host input changed during execution")
                self.state.commit(case_id, revision, update, operation_id, OperationReceipt(request_hash=request_hash, result=result))
                if changed:
                    self._services[key] = candidate
                return result
            except Exception as error:
                # Another worker may already have committed this exact operation.
                if request_hash and isinstance(error, WorkflowConflict):
                    receipt = self.state.read_operation(case_id, operation_id)
                    if receipt and receipt.request_hash == request_hash:
                        return receipt.result.model_copy(deep=True)
                return self._error(operation_id, error)

    def _dispatch(self, tools, snapshot, inp, old, action, actor, operation_id):
        targets = set(inp.target_item_ids)
        all_items = {i.item_id for i in inp.items}
        input_hash = self._input_hash(snapshot, inp)
        same_input = bool(snapshot.a3_ref and snapshot.a3_ref.input_hash == input_hash)
        if isinstance(action, EstimateAction):
            selected = set(action.args.item_ids)
            if not selected <= all_items:
                raise AdapterError("INVALID_ARGUMENT", "현재 요청에 없는 항목입니다.")
            if not old or tools.source_input(old["session_id"]).context != inp.context:
                if selected != targets:
                    raise AdapterError("REQUIRES_FULL_REESTIMATION", "최초·새 실행에서는 전체 산정 검토 대상을 선택해야 합니다.")
                view = tools.estimate_impact(inp)
                return view, sorted(targets), True
            if same_input and old["standards_current"] and old["context_current"]:
                return old, [], False
            if selected == targets:
                # Global changes and standards updates may require a fresh session.
                view = tools.estimate_impact(inp)
                return view, sorted(targets), True
            view = tools.reestimate(old["session_id"], Reestimate(state_revision=old["state_revision"], input=inp, item_ids=action.args.item_ids))
            return view, view["processed_item_ids"], True
        if isinstance(action, DocumentAction):
            if set(action.args.item_ids) != all_items:
                raise AdapterError("UNSUPPORTED_DOCUMENT_SCOPE", "v1 문서는 요청 전체 항목을 대상으로 생성합니다.")
            if not old:
                if targets:
                    raise AdapterError("UPSTREAM_NOT_READY", "먼저 잠정 산정을 실행해야 합니다.")
                old = tools.estimate_impact(inp)
            elif not same_input:
                raise AdapterError("UPSTREAM_NOT_READY", "입력이 변경되어 다시 산정해야 합니다.")
            if action.args.kind == "scope_response" and old["impact"]["estimates"]:
                raise AdapterError("INVALID_ARGUMENT", "변경이 포함된 요청은 협의 메일로 작성해야 합니다.")
            if action.args.kind == "change_agreement" and not old["impact"]["estimates"]:
                raise AdapterError("INVALID_ARGUMENT", "산정 결과가 없는 요청은 합의서를 생성하지 않습니다.")
            document = DOCUMENT_KEYS[action.args.kind]
            complete = "email" in old["documents"] and (not old["impact"]["estimates"] or "agreement" in old["documents"])
            if complete:
                return old, [], False
            view = tools.draft_documents(old["session_id"], DraftDocuments(state_revision=old["state_revision"], kind="bundle" if old["impact"]["estimates"] else "scope_reply"))
            return view, sorted(all_items), True
        if not old:
            raise AdapterError("SESSION_LOST", "Agent 3 세션이 없습니다. 전체 산정을 다시 실행해 주세요.")
        if not same_input:
            raise WorkflowConflict("upstream input changed before PM action")
        sid, rev = old["session_id"], old["state_revision"]
        if isinstance(action, PmRecalculate):
            view = tools.recalculate(sid, Recalculate(state_revision=rev, **action.args.model_dump()))
        elif isinstance(action, PmEdit):
            view = tools.edit_document(sid, EditDocument(state_revision=rev, kind=DOCUMENT_KEYS[action.kind],
                narrative=action.narrative, reason=action.reason, editor_ref=actor))
        else:
            if action.expected_snapshot_hash != old["snapshot_hash"]:
                raise WorkflowConflict("PM reviewed an outdated snapshot")
            view = tools.review(sid, FinalReview(state_revision=rev, snapshot_hash=old["snapshot_hash"],
                request_id=operation_id, reviewer_ref=actor, decision=action.decision,
                reason=action.reason, acknowledge_unknown_schedule=action.acknowledge_unknown_schedule))
        return view, sorted(all_items), True

    @staticmethod
    def _result(operation_id, action, inp, view, processed):
        needs = []
        for need in view["needs"]:
            identity = {"context": inp.context.model_dump(), "kind": need["kind"], "item_ids": sorted(need["item_ids"]),
                "topic": need["topic"], "target": need["target"], "source": need["source"], "question_id": need["question_id"]}
            needs.append({"need_id": f"a3-need-{_hash(identity)[:24]}", **need})
        blocked = any(n["blocking"] for n in needs)
        status = "needs_input" if blocked else "ready"
        if isinstance(action, EstimateAction) and not view["impact"]["estimates"] and not blocked:
            status = "not_required"
        ids = [view["impact"]["meta"]["result_id"]]
        if isinstance(action, DocumentAction):
            ids = [view["documents"][DOCUMENT_KEYS[action.args.kind]]["result_id"]]
        elif isinstance(action, PmEdit):
            ids = [view["documents"][DOCUMENT_KEYS[action.kind]]["result_id"]]
        return ToolResult(operation_id=operation_id, status=status,
            requested_item_ids=action.args.item_ids if isinstance(action, (EstimateAction, DocumentAction)) else [],
            processed_item_ids=processed, result_ids=ids,
            summary="추가 확인이 필요합니다." if blocked else "요청한 Agent 3 작업을 완료했습니다.", needs=needs)

    @staticmethod
    def _input_hash(snapshot, inp):
        return _hash({"input": inp.model_dump(mode="json"), "a1_result_id": snapshot.a1_result_id})

    @staticmethod
    def _update(snapshot, inp, view, old, processed):
        questions = [Question.model_validate(q) for q in view["impact"]["questions"]]
        active = {q.question_id for q in questions}
        resolved, superseded = [], []
        for q in (old or {}).get("impact", {}).get("questions", []):
            scope = set(q["item_ids"]) or {i.item_id for i in inp.items}
            if q["question_id"] not in active and scope <= set(processed):
                still_blocked = any(n["blocking"] and scope & set(n["item_ids"]) for n in view["needs"])
                (superseded if still_blocked else resolved).append(q["question_id"])
        return Agent3Update(a3_ref=Agent3Reference(session_id=view["session_id"], state_revision=view["state_revision"],
            snapshot_hash=view["snapshot_hash"], input_hash=Agent3SupervisorAdapter._input_hash(snapshot, inp),
            execution_config_revision=snapshot.execution_config_revision), view=view, rate_card_id=inp.rate_card_id,
            source_result_ids={"agent1": snapshot.a1_result_id, "agent2": inp.a2_result.meta.result_id,
                "agent3": view["impact"]["meta"]["result_id"]}, active_questions=questions,
            resolved_question_ids=resolved, superseded_question_ids=superseded)

    @staticmethod
    def _error(operation_id, error):
        if isinstance(error, AdapterError):
            detail = ToolError(code=error.code, message=str(error))
        elif isinstance(error, FullReestimationRequired):
            detail = ToolError(code="REQUIRES_FULL_REESTIMATION", message="전역 변경으로 전체 재산정이 필요합니다.")
        elif isinstance(error, WorkflowConflict):
            detail = ToolError(code="STATE_CONFLICT", message="실행 상태가 변경됐습니다. 최신 상태를 확인해 주세요.")
        elif isinstance(error, AgentFailure):
            detail = ToolError(code=error.error.code, message=error.error.message,
                retryable=error.error.retryable or error.error.code == "OUTPUT_INVALID")
        elif isinstance(error, (ValidationError, ValueError)):
            detail = ToolError(code="INVALID_ARGUMENT", message="도구 인자 또는 입력 조건을 확인해 주세요.")
        elif isinstance(error, KeyError):
            detail = ToolError(code="SESSION_LOST", message="실행 참조가 없습니다. 최신 상태에서 전체 산정을 실행해 주세요.")
        else:
            detail = ToolError(code="INTERNAL_ERROR", message="Agent 3 연결 계층에서 처리에 실패했습니다.")
        return ToolResult(operation_id=operation_id or "", status="error", summary=detail.message, error=detail)
