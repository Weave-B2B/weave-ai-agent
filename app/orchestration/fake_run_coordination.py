"""Strict reference implementation of the proposed coordination contract.

For tests/local contract validation only; it does not replace Backend persistence.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from copy import deepcopy
from pydantic import ValidationError
from schema.schemas import Answer

from app.orchestration.fake_run_state import FakeRunStateClient
from app.orchestration.run_state import (
    AGENT_PREREQUISITES, InvalidStateTransition, PendingReview,
)
from app.orchestration.run_coordination import (
    AcquireExecution, AnswerQuestion, CoordinatedRun, CoordinationState, ExecutionLease,
    ExecutionReceipt, FinalReviewDecision, FinishUnchanged, FencedCommit, RequestFinalReview,
    ReviewBundle, SaveCoordination, payload_fingerprint,
    ReserveLlmAttempt, ResumeRun,
)
from app.orchestration.run_agent3_payload import Agent3RunOutput


class FakeCoordinatedRunStateClient(FakeRunStateClient):
    def __init__(self, *, clock=None):
        super().__init__(strict_commit=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_run(self, run_id, inputs=None):
        with self._lock:
            if run_id in self._runs:
                raise InvalidStateTransition("Run already exists", run_id=run_id)
            super().create_run(run_id, inputs)
            run = self._get(run_id)
            run.coordination = CoordinationState()
            run.executions = {}
            run.review_bundle = None

    def get_coordinated_run(self, run_id):
        with self._lock:
            run = self._get(run_id)
            return CoordinatedRun(state=self._snapshot(run), coordination=run.coordination,
                                  executions=run.executions, review_bundle=run.review_bundle).model_copy(deep=True)

    def reserve_llm_attempt(self, req: ReserveLlmAttempt):
        def apply(run):
            state = run.coordination
            if run.status not in ("QUEUED", "RUNNING") or state.hold_reason:
                raise InvalidStateTransition("현재 상태에서는 모델을 호출할 수 없습니다.", run_id=run.run_id)
            if state.llm_calls >= 80 or state.segment_llm_calls >= 40:
                raise InvalidStateTransition("모델 호출 한도에 도달했습니다(구간 40회 / 요청 80회).", run_id=run.run_id)
            state.llm_calls += 1
            state.segment_llm_calls += 1
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def resume_run(self, req: ResumeRun):
        def apply(run):
            if run.executions or run.status == "COMPLETED" or any(q.blocking and q.status == "open" for q in run.coordination.questions):
                raise InvalidStateTransition("미해결 질문 또는 실행 상태를 먼저 확인해 주세요.", run_id=run.run_id)
            last_review = next((e for e in reversed(run.history) if e["event"] == "final_review"), None)
            if not (run.coordination.hold_reason or run.status == "FAILED" or
                    (last_review and last_review["decision"] in ("hold", "reject"))):
                raise InvalidStateTransition("재개할 보류 또는 실패 상태가 없습니다.", run_id=run.run_id)
            if run.coordination.llm_calls >= 80:
                raise InvalidStateTransition("요청 전체 호출 한도에 도달했습니다. 새 요청으로 검토해 주세요.", run_id=run.run_id)
            run.status, run.pending, run.review_bundle = "RUNNING", None, None
            run.coordination.hold_reason = None
            run.coordination.segment_steps = run.coordination.segment_llm_calls = 0
            run.history.append({"event": "run_resumed", "resumed_by": req.resumed_by, "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def get_state(self, run_id):
        return super().get_state(run_id).model_copy(deep=True)

    def get_inputs(self, run_id):
        return super().get_inputs(run_id).model_copy(deep=True)

    def _write(self, req, apply):
        return super()._write(req, apply).model_copy(deep=True)

    def get_result(self, run_id, result_id):
        return super().get_result(run_id, result_id).model_copy(deep=True)

    def history(self, run_id):
        with self._lock:
            return deepcopy(super().history(run_id))

    def start_agent(self, req):
        raise InvalidStateTransition("Use acquire_execution for coordinated runs", run_id=req.run_id)

    def acquire_execution(self, req: AcquireExecution):
        def apply(run):
            if run.pending or run.status in ("WAITING_INPUT", "WAITING_PM_REVIEW"):
                raise InvalidStateTransition("Resolve pending input/review first", run_id=run.run_id)
            if any(q.blocking and q.status == "open" for q in run.coordination.questions):
                raise InvalidStateTransition("Blocking PM questions must be answered first", run_id=run.run_id)
            active = run.executions.get(req.agent_type)
            if run.agents[req.agent_type].status == "RUNNING":
                if not active or active.expires_at > self._clock() or not req.recover_expired:
                    raise InvalidStateTransition("Execution is still owned by another worker", run_id=run.run_id)
            upstream = self._upstream(run, req.agent_type)
            self._check_transition(run, "RUNNING")
            token = uuid4().hex
            run.executions[req.agent_type] = ExecutionLease(
                token=token, expires_at=self._clock() + timedelta(seconds=req.lease_seconds),
                upstream_result_ids=upstream)
            run.agents[req.agent_type] = run.agents[req.agent_type].model_copy(update={"status": "RUNNING"})
            run.status, run.review_bundle = "RUNNING", None
            run.history.append({"event": "execution_acquired", "agent_type": req.agent_type,
                                "operation_id": req.operation_id})
            receipt = self._receipt(run, req.operation_id)
            return ExecutionReceipt(**receipt.model_dump(), execution_token=token)
        return self._write(req, apply)

    def _upstream(self, run, agent):
        upstream = []
        for parent in AGENT_PREREQUISITES[agent]:
            state = run.agents[parent]
            if state.status != "COMPLETED" or state.stale:
                raise InvalidStateTransition("Prerequisite result is unavailable or stale", run_id=run.run_id)
            upstream.append(state.current_result_id)
        return upstream

    def _execution(self, run, agent, token):
        lease = run.executions.get(agent)
        if (not lease or lease.token != token or lease.expires_at <= self._clock()
                or run.agents[agent].status != "RUNNING"):
            raise InvalidStateTransition("Execution token expired or replaced", run_id=run.run_id)
        if lease.upstream_result_ids != self._upstream(run, agent):
            raise InvalidStateTransition("Upstream changed during execution", run_id=run.run_id)
        return lease

    def commit_result(self, req: FencedCommit):
        if not isinstance(req, FencedCommit) or req.requested_run_status == "COMPLETED":
            raise InvalidStateTransition("Fenced commit required; only final_review may complete Run", run_id=req.run_id)
        with self._lock:
            run = self._get(req.run_id)
            # Replayed writes retain the original receipt even after the lease closes.
            if req.operation_id not in run.receipts:
                self._execution(run, req.agent_type, req.execution_token)
            receipt = super().commit_result(req.model_copy(deep=True))
            if not receipt.replayed:
                run.executions.pop(req.agent_type, None)
                run.review_bundle = None
            return receipt

    def finish_unchanged(self, req: FinishUnchanged):
        def apply(run):
            lease = self._execution(run, req.agent_type, req.execution_token)
            state = run.agents[req.agent_type]
            if state.stale or state.current_result_id != req.target_result_id:
                raise InvalidStateTransition("No unchanged current result", run_id=run.run_id)
            old = run.results[req.target_result_id]
            if (old.upstream_result_ids != lease.upstream_result_ids
                    or payload_fingerprint(old.result_payload) != payload_fingerprint(req.observed_payload)):
                raise InvalidStateTransition("Changed output/upstream must create a new result", run_id=run.run_id)
            run.agents[req.agent_type] = state.model_copy(update={"status": "COMPLETED"})
            run.executions.pop(req.agent_type)
            run.history.append({"event": "execution_unchanged", "agent_type": req.agent_type,
                                "result_id": req.target_result_id, "operation_id": req.operation_id})
            return self._receipt(run, req.operation_id, old.result_id, old.result_revision)
        return self._write(req, apply)

    def save_coordination(self, req: SaveCoordination):
        def apply(run):
            old = run.coordination
            new = CoordinationState.model_validate(req.state.model_dump())
            if (new.llm_calls < old.llm_calls or new.segment_llm_calls < old.segment_llm_calls
                    or new.segment_steps < old.segment_steps):
                raise InvalidStateTransition("Counters cannot be reset by a state update", run_id=run.run_id)
            for key, count in old.research_attempts.items():
                if new.research_attempts.get(key, 0) < count:
                    raise InvalidStateTransition("Research counters cannot decrease", run_id=run.run_id)
            if new.answers != old.answers:
                raise InvalidStateTransition("Answers must use the atomic answer endpoint", run_id=run.run_id)
            by_id = {q.question_id: q for q in new.questions}
            for previous in old.questions:
                current = by_id.get(previous.question_id)
                if current != previous:
                    raise InvalidStateTransition("Existing questions/answers must be preserved", run_id=run.run_id)
            added = [q for q in new.questions if q.question_id not in {q.question_id for q in old.questions}]
            if len(new.questions) > 4 or any(q.status != "open" or q.answer is not None for q in added):
                raise InvalidStateTransition("At most four new, unanswered questions per request version", run_id=run.run_id)
            if any(q.blocking and q.status == "open" for q in new.questions):
                if run.executions:
                    raise InvalidStateTransition("Close execution before publishing PM questions", run_id=run.run_id)
                run.status = "WAITING_INPUT"
            if old.questions != new.questions or old.quote_links != new.quote_links:
                run.review_bundle = None
                if run.pending:
                    run.pending = None
                    if run.status == "WAITING_PM_REVIEW":
                        run.status = "RUNNING"
                if old.quote_links != new.quote_links and run.agents["agent3"].current_result_id:
                    run.agents["agent3"] = run.agents["agent3"].model_copy(update={"stale": True})
            run.coordination = new
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def answer_question(self, req: AnswerQuestion):
        def apply(run):
            q = next((q for q in run.coordination.questions if q.question_id == req.question_id), None)
            if not q or q.status != "open" or run.status != "WAITING_INPUT":
                raise InvalidStateTransition("Question is not open for answers", run_id=run.run_id)
            answer = req.answer.strip()
            if not answer:
                raise InvalidStateTransition("Answer must not be blank", run_id=run.run_id)
            unknown = req.disposition == "unknown" or answer.casefold() in {"모름", "모르겠어요", "unknown", "확인 필요"}
            if not unknown and answer not in q.choices and not q.allow_other:
                raise InvalidStateTransition("Select one of the allowed choices", run_id=run.run_id)
            # Unknown is recorded for audit, never treated as a resolved blocker.
            q.answer, q.status = answer, "open" if unknown else "resolved"
            run.coordination.answers.append(Answer(answer_id=f"answer-{req.operation_id}",
                question_id=q.question_id, text=answer, answered_by="pm",
                answered_at=self._clock().isoformat(), verified=False))
            run.history.append({"event": "question_answered", "question_id": q.question_id,
                                "answered_by": req.answered_by, "answer": answer,
                                "resolved": not unknown, "operation_id": req.operation_id})
            if not any(q.blocking and q.status == "open" for q in run.coordination.questions):
                run.status, run.pending = "RUNNING", None
                if not unknown:
                    run.coordination.segment_steps = 0
                    run.coordination.segment_llm_calls = 0
            run.review_bundle = None
            # An answer may affect the analysis: keep history, invalidate existing outputs.
            if not unknown:
                affected = ("agent1", "agent2", "agent3") if q.stage == "agent1" else ("agent2", "agent3")
                for agent in affected:
                    if run.agents[agent].current_result_id:
                        run.agents[agent] = run.agents[agent].model_copy(update={"stale": True})
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def _bundle(self, run):
        if run.executions or any(a.status != "COMPLETED" or a.stale for a in run.agents.values()):
            raise InvalidStateTransition("Final review requires current completed results", run_id=run.run_id)
        if any(q.blocking and q.status != "resolved" for q in run.coordination.questions):
            raise InvalidStateTransition("Unanswered blocking questions", run_id=run.run_id)
        ids = {stage: state.current_result_id for stage, state in run.agents.items()}
        payload = run.results[ids["agent3"]].result_payload
        try:
            output = Agent3RunOutput.model_validate(payload)
        except ValidationError as exc:
            raise InvalidStateTransition("Stored Agent 3 result is not reviewable", run_id=run.run_id) from exc
        if (output.status not in ("IMPACT_DRAFT_READY", "NOT_REQUIRED")
                or any(n.blocking for n in output.needs)
                or any(q.blocking and q.status != "resolved" for q in output.questions)):
            raise InvalidStateTransition("Agent 3 still requires input", run_id=run.run_id)
        documents = output.documents
        if not documents or not any(d.body.strip() for d in documents.values()):
            raise InvalidStateTransition("Final review requires a customer document draft", run_id=run.run_id)
        if any(d.a2_result_id != ids["agent2"] or d.impact_result_id != output.meta.result_id
               for d in documents.values()):
            raise InvalidStateTransition("Document provenance differs from reviewed results", run_id=run.run_id)
        return ReviewBundle(result_ids=ids, document_fingerprint=payload_fingerprint(
            {key: doc.model_dump(mode="json") for key, doc in documents.items()}))

    def request_final_review(self, req: RequestFinalReview):
        def apply(run):
            if run.status != "RUNNING" or self._bundle(run) != req.bundle:
                raise InvalidStateTransition("Final review bundle is outdated", run_id=run.run_id)
            run.review_bundle = req.bundle.model_copy(deep=True)
            run.pending = PendingReview(agent_type="agent3", target_result_id=req.bundle.result_ids["agent3"],
                                        reason="Final result and customer document review")
            run.status = "WAITING_PM_REVIEW"
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def final_review(self, req: FinalReviewDecision):
        def apply(run):
            if (run.status != "WAITING_PM_REVIEW" or run.review_bundle != req.bundle
                    or self._bundle(run) != req.bundle):
                raise InvalidStateTransition("Review targets an outdated bundle", run_id=run.run_id)
            run.history.append({"event": "final_review", "decision": req.decision,
                                "reviewed_by": req.reviewed_by, "bundle": req.bundle.model_dump(),
                                "operation_id": req.operation_id})
            if req.decision != "hold":
                run.status = "COMPLETED" if req.decision == "approve" else "RUNNING"
                run.pending, run.review_bundle = None, None
            return self._receipt(run, req.operation_id)
        return self._write(req, apply)

    def resolve_review(self, req):
        raise InvalidStateTransition("Use answer_question or final_review for coordinated runs", run_id=req.run_id)

    def request_review(self, req):
        raise InvalidStateTransition("Use coordination questions or request_final_review", run_id=req.run_id)

    def submit_input(self, req):
        raise InvalidStateTransition("Use answer_question; PMReview is the legacy execution contract", run_id=req.run_id)

    def record_edit(self, req):
        with self._lock:
            run = self._get(req.run_id)
            if run.executions:
                raise InvalidStateTransition("Close running executions before editing", run_id=req.run_id)
            receipt = super().record_edit(req.model_copy(deep=True))
            if not receipt.replayed:
                run.pending, run.review_bundle = None, None
                run.status = ("WAITING_INPUT" if any(q.blocking and q.status == "open"
                              for q in run.coordination.questions) else "RUNNING")
                # The receipt must describe the post-edit state, including reopened completion.
                receipt.status = run.status
                digest, _ = run.receipts[req.operation_id]
                run.receipts[req.operation_id] = (digest, receipt.model_copy(deep=True))
            return receipt
