"""Run-backed execution of the existing orchestrator decision/tool contracts.

No authoritative local session/cache: each step reads committed results. The
legacy prototype Supervisor remains available until its UI is explicitly moved.
"""
from uuid import uuid4

from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot, Agent2OrchestratorAdapter, SCOPE_INTENTS
from app.agents.agent3.contracts import ProvisionalAgent3Output, WorkbookReference
from app.agents.agent3.effort import load_effort_rules
from app.agents.agent3.orchestration import Agent3Orchestration, DraftDocuments, Reestimate
from app.agents.agent3.orchestrator_adapter import Agent3CaseSnapshot
from app.agents.agent3.workflow import _hash, _rate_hash
from app.tools.calculation_snapshot import validate_output_pricing
from app.tools.errors import ToolInputError
from app.tools.backend_calc import BackendCalcError
from app.core.config import settings
from app.agents.base import AgentFailure
from app.orchestration.coordinated_execution import CoordinatedAgentRunner
from app.orchestration.decision import choose_action, InvalidDecision
from app.orchestration.run_agent3_payload import Agent2RunOutput, Agent3RunOutput, Agent3Source
from app.orchestration.run_coordination import (
    AnswerQuestion, FinalReviewDecision, PMQuestion, RequestFinalReview, ReviewBundle,
    SaveCoordination, payload_fingerprint,
    ReserveLlmAttempt, MAX_QUESTION_CHOICES, clean_choices,
)
from app.orchestration.run_state import InvalidStateTransition, StateRevisionConflict
from app.orchestration.tool_state import SharedToolState
from schema.schemas import Agent1Output, Agent2Output


def _as(model, payload):
    return model.model_validate({k: v for k, v in payload.items() if k in model.model_fields})


def _question_key(item_ids, field):
    return tuple(sorted(item_ids)), (field or "").strip().casefold()


def _question_text_key(text):
    """Same question re-worded only by spacing/punctuation is the same question."""
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


class RunSupervisorRuntime:
    def __init__(self, state, agents, llm, draft_llm):
        # Agent 1은 개별 호출과 같은 인스턴스·프롬프트를 그대로 쓴다(프롬프트 차이는 버전 파일로만 관리).
        self.state, self.agents, self.llm, self.draft_llm = state, agents, llm, draft_llm
        self.runner = CoordinatedAgentRunner(state)

    def _request(self, cls, run, **kw):
        return cls(run_id=run.state.run_id, expected_state_revision=run.state.state_revision,
                   operation_id=uuid4().hex, **kw)

    def _save(self, run, coordination):
        req = self._request(SaveCoordination, run, state=coordination)
        self.runner._write(self.state.save_coordination, req)
        return self.state.get_coordinated_run(run.state.run_id)

    def _result(self, run, stage, model):
        rid = run.state.agents[stage].current_result_id
        return _as(model, self.state.get_result(run.state.run_id, rid).result_payload) if rid else None

    def _inputs(self, run):
        original = self.state.get_inputs(run.state.run_id)
        unique = {a.answer_id: a for a in original.agent1_input.answers + run.coordination.answers}
        return original, original.agent1_input.model_copy(update={"answers": list(unique.values())}, deep=True)

    @staticmethod
    def _research_key(run, item_id):
        # A new result ID from another search must not reset the allowance.
        # Original Run input/baseline is immutable; new A1 or PM answers form a new scope.
        return "scope-" + _hash([run.state.run_id, run.state.agents["agent1"].current_result_id,
                                 [a.answer_id for a in run.coordination.answers], item_id])[:24]

    def _attempts(self, run, item_id):
        return run.coordination.research_attempts.get(self._research_key(run, item_id), 0)

    def _questions(self, run, outputs):
        """Reconcile saved worker questions before permitting another worker.

        This also repairs a crash after result commit but before question publish.
        """
        coord = run.coordination.model_copy(deep=True)
        known = {q.question_id for q in coord.questions}
        keys = {_question_key(q.item_ids, q.field) for q in coord.questions}
        # 에이전트가 답변 후 다른 ID·필드로 같은 질문을 다시 내도(에이전트 간 중복 포함) 같은 질문으로 본다.
        texts = {_question_text_key(q.text) for q in coord.questions}
        candidates = []

        def previous_of(qid, item_ids, field, text):
            return next((p for p in coord.questions if p.question_id == qid
                         or _question_key(p.item_ids, p.field) == _question_key(item_ids, field)
                         or _question_text_key(p.text) == _question_text_key(text)), None)

        def unresolved_hold(previous):
            # 이미 답한 필수 질문이 다시 필수로 나오면 같은 질문을 되풀이하지 않고 보류한다.
            if previous.disposition == "unknown":
                coord.hold_reason = (f"필수 확인 사항이 '모름'으로 남아 분석을 보류합니다: {previous.text} "
                                     "담당자가 확인한 뒤 요청 내용을 보완해 다시 진행해 주세요.")
            else:
                coord.hold_reason = "앞선 답변으로 필수 질문이 해소되지 않았습니다. 담당자 추가 검토가 필요합니다."

        for stage, output in outputs.items():
            if output is None or run.state.agents[stage].stale or run.state.agents[stage].status != "COMPLETED":
                continue
            for q in output.questions:
                # Optional A3 suggestions belong in the result/draft, not a new
                # blocking PM round after every otherwise-complete estimate.
                if stage in ("agent1", "agent3") and not q.blocking:
                    continue
                field = q.field or f"question:{q.question_id}"
                previous = previous_of(q.question_id, q.item_ids, field, q.text)
                if (previous and previous.status == "resolved" and q.blocking
                        and (output.status == "NEEDS_INPUT" or previous.disposition == "unknown")):
                    unresolved_hold(previous)
                if q.status in ("open", "answered"):
                    candidates.append(PMQuestion(question_id=q.question_id, item_ids=q.item_ids,
                        field=field, text=q.text, stage=stage, reason=q.reason, basis_refs=q.basis_refs,
                        target=q.target, choices=clean_choices(q.choices)))
            if stage == "agent2":
                for assessment in output.assessments:
                    if assessment.label == "unclear" and not any(assessment.item_id in q.item_ids for q in output.questions):
                        candidates.append(PMQuestion(question_id=f"scope-{assessment.item_id}",
                            item_ids=[assessment.item_id], field="scope_missing", stage=stage,
                            text=" / ".join(assessment.missing_info) or "범위를 판단할 추가 정보를 확인해 주세요.",
                            reason=assessment.rationale or "계약 범위를 판단할 사실이 부족합니다.",
                            basis_refs=[f"{e.doc_id}:{e.section_id}" for e in assessment.evidence]))
            if stage == "agent3":
                for need in output.needs:
                    exhausted = any(self._attempts(run, i) >= 1 for i in need.item_ids)
                    if not need.blocking or (need.kind == "more_evidence" and not exhausted):
                        continue
                    if need.question_id and need.question_id in known:
                        continue
                    if need.kind == "missing_standard" and any(
                        q.stage == stage and set(q.item_ids) == set(need.item_ids)
                        and any(tag in q.field.casefold() for tag in ("공수", "기준", "effort", "rate", "standard"))
                        for q in candidates):
                        continue
                    field = f"need:{need.kind}:{' '.join(need.topic.split()).casefold()}"
                    qid = need.question_id or f"run-q-{_hash([need.item_ids, field])[:20]}"
                    previous = previous_of(qid, need.item_ids, field, need.topic)
                    if previous and previous.status == "resolved":
                        unresolved_hold(previous)
                    candidates.append(PMQuestion(question_id=qid, item_ids=need.item_ids,
                        field=field, text=need.topic, stage=stage,
                        reason=need.reason or "영향 산정을 진행하려면 확인이 필요합니다.",
                        target="client" if need.target == "client" else "pm",
                        choices=clean_choices(need.choices)))
        for q in candidates:
            key, text_key = _question_key(q.item_ids, q.field), _question_text_key(q.text)
            if q.question_id in known or key in keys or text_key in texts:
                continue
            if len(coord.questions) >= 4:
                coord.hold_reason = "추가 질문 한도에 도달했습니다. 담당자 검토가 필요합니다."
                break
            coord.questions.append(q)
            known.add(q.question_id)
            keys.add(key)
            texts.add(text_key)
        return self._save(run, coord) if coord != run.coordination else run

    def step(self, run_id, *, expected_revision=None):
        from app.llm.attempts import account_attempts
        def reserve():
            operation_id = uuid4().hex
            for attempt in range(3):
                state = self.state.get_state(run_id)
                request = ReserveLlmAttempt(run_id=run_id, expected_state_revision=state.state_revision,
                                            operation_id=operation_id)
                try:
                    return self.runner._write(self.state.reserve_llm_attempt, request)
                except StateRevisionConflict:
                    if attempt == 2:
                        raise
        with account_attempts(reserve):
            try:
                return self._step(run_id, expected_revision=expected_revision)
            except (AgentFailure, InvalidStateTransition):
                run = self.state.get_coordinated_run(run_id)
                if run.coordination.segment_llm_calls < 40 and run.coordination.llm_calls < 80:
                    raise
                coord = run.coordination.model_copy(deep=True)
                coord.hold_reason = ("요청 전체 모델 호출 80회 한도에 도달했습니다. 새 요청으로 검토해 주세요."
                                     if coord.llm_calls >= 80 else "연속 모델 호출 40회 한도에 도달했습니다. 결과 확인 후 명시적으로 재개해 주세요.")
                return {"status": "waiting", "reason": coord.hold_reason, "run": self._save(run, coord)}

    def _step(self, run_id, *, expected_revision=None):
        run = self.state.get_coordinated_run(run_id)
        if expected_revision is not None and run.state.state_revision != expected_revision:
            raise StateRevisionConflict("Run changed before supervisor step", run_id=run_id,
                expected_revision=expected_revision, actual_revision=run.state.state_revision)
        if run.state.status == "COMPLETED":
            return {"status": "completed", "run": run}
        outputs = {stage: self._result(run, stage, model) for stage, model in (
            ("agent1", Agent1Output), ("agent2", Agent2RunOutput), ("agent3", Agent3RunOutput))}
        if run.coordination.quote_links and run.coordination.quote_source_result_id != run.state.agents["agent1"].current_result_id:
            coord = run.coordination.model_copy(deep=True)
            coord.quote_links = {}
            coord.quote_source_result_id = None
            run = self._save(run, coord)
        run = self._questions(run, outputs)
        if run.state.status in ("WAITING_INPUT", "WAITING_PM_REVIEW"):
            return {"status": "waiting_input" if run.state.status == "WAITING_INPUT" else "waiting_final_review", "run": run}
        if run.coordination.hold_reason:
            return {"status": "waiting", "reason": run.coordination.hold_reason, "run": run}
        if run.executions:
            return {"status": "running", "run": run}
        if run.coordination.segment_steps >= 12:
            coord = run.coordination.model_copy(deep=True)
            coord.hold_reason = "연속 실행 12회 한도에 도달했습니다. 진행 내용을 확인해 주세요."
            return {"status": "waiting", "run": self._save(run, coord), "reason": coord.hold_reason}
        original, inp = self._inputs(run)
        a1, a2, a3 = (outputs[stage] for stage in ("agent1", "agent2", "agent3"))
        if not a1:
            return self._execute(run, "analyze_request", lambda: self._analyze(inp))
        items = [i for i in a1.items if i.item_type not in ("constraint", "deadline", "dependency")]
        constraints = [i for i in a1.items if i not in items]
        ids = {i.item_id for i in items}
        scope_ids = {i.item_id for i in items if i.intent in SCOPE_INTENTS}
        assessed = {a.item_id for a in a2.assessments} if a2 else set()
        if a2:
            check = Agent2CaseSnapshot(state_revision=0, context=inp.context, message_text=inp.message_text,
                a1_result_id=run.state.agents["agent1"].current_result_id, items=items,
                constraints=constraints, answers=inp.answers)
            assessed &= {i.item_id for i in items if a2.input_item_hashes.get(i.item_id) == check.item_hash(i)}
        quote_ids = {i.item_id for i in items if i.intent == "estimate_request"}
        unlinked = quote_ids - run.coordination.quote_links.keys()
        a1_current = run.state.agents["agent1"].status == "COMPLETED" and not run.state.agents["agent1"].stale
        a2_current = bool(a1_current and a2 and scope_ids <= assessed and not run.state.agents["agent2"].stale
                          and run.state.agents["agent2"].status == "COMPLETED")
        a3_current = bool(a2_current and a3 and a3.source and not run.state.agents["agent3"].stale
                          and run.state.agents["agent3"].status == "COMPLETED")
        if a3_current and a3.source:
            a3_current = (a3.source.rate_card_id == original.rate_card_id
                          and a3.source.calculation_context_revision == original.calculation_context_revision
                          and a3.source.rate_hash == _rate_hash(original.rate_card_id)
                          and a3.source.effort_hash == _hash(load_effort_rules())
                          and a3.source.a1_result_id == run.state.agents["agent1"].current_result_id
                          and a3.source.a2_result_id == run.state.agents["agent2"].current_result_id
                          and a3.source.answer_ids == [a.answer_id for a in inp.answers]
                          and a3.source.quote_links == run.coordination.quote_links)
        if a3_current and a3.documents and not any(n.blocking for n in a3.needs):
            self._validate_review_pricing(a3)
            target = ReviewBundle(result_ids={s: a.current_result_id for s, a in run.state.agents.items()},
                document_fingerprint=payload_fingerprint({k: v.model_dump(mode="json") for k, v in a3.documents.items()}))
            self.state.request_final_review(self._request(RequestFinalReview, run, bundle=target))
            return {"status": "waiting_final_review", "run": self.state.get_coordinated_run(run_id)}
        if not ids:
            coord = run.coordination.model_copy(deep=True)
            coord.hold_reason = "분석할 요청 항목이 없습니다."
            return {"status": "waiting", "run": self._save(run, coord)}
        eligible = {i for i in scope_ids if not a2_current or i not in assessed
                    or (a3_current and any(n.kind == "more_evidence" and i in n.item_ids for n in a3.needs)
                        and self._attempts(run, i) < 1)}
        targets = {i.item_id for i in items if i.intent == "change_request" or
                   (i.intent == "confirmation" and i.conditional_followup)}
        targets.update(i for linked in run.coordination.quote_links.values() for i in linked)
        allowed = {"hold"}
        if not a1_current:
            allowed.add("analyze_request")
        else:
            if eligible:
                allowed.add("assess_scope")
            if unlinked:
                allowed.add("link_quote")
            if a2_current and scope_ids <= assessed and not unlinked:
                if not a3_current:
                    allowed.add("estimate_impact") if targets else allowed.add("draft_documents")
                elif not any(n.blocking for n in a3.needs):
                    allowed.add("draft_documents")
        if not a2 and not scope_ids:
            return self._execute(run, "assess_scope", lambda: Agent2RunOutput(
                context=inp.context, status="READY_FOR_REVIEW", assessments=[]))
        summary = {"items": [i.model_dump(mode="json") for i in items], "message_text": inp.message_text,
            "scope": a2.model_dump(mode="json") if a2 else None,
            "impact": a3.model_dump(mode="json") if a3 else None, "impact_stale": bool(a3 and not a3_current),
            "needs": [n.model_dump() for n in a3.needs] if a3_current else [],
            "answers": [a.model_dump(mode="json") for a in inp.answers],
            "quote_links": run.coordination.quote_links, "answer_pending_review": bool(a2 and not a2_current)}
        coord = run.coordination.model_copy(deep=True)
        coord.segment_steps += 1
        run = self._save(run, coord)
        try:
            choice = choose_action(self.llm, state_summary=summary, allowed_actions=allowed, item_ids=ids,
                scope_item_ids=eligible, assessed_item_ids=assessed, estimate_item_ids=targets)
        except InvalidDecision:
            run = self.state.get_coordinated_run(run_id)
            coord = run.coordination.model_copy(deep=True)
            coord.hold_reason = "총괄의 다음 작업 선택을 검증하지 못했습니다. 진행 내용을 확인해 주세요."
            return {"status": "waiting", "reason": coord.hold_reason, "run": self._save(run, coord)}
        fresh = self.state.get_coordinated_run(run_id)
        signature = lambda r: [r.state.agents, r.coordination.questions, r.coordination.quote_links,
                               r.coordination.answers, r.coordination.hold_reason]
        if signature(fresh) != signature(run):
            raise StateRevisionConflict("총괄 판단 중 실행 상태가 변경되었습니다.", run_id=run_id)
        run, coord = fresh, fresh.coordination.model_copy(deep=True)
        if choice.action == "hold":
            coord.hold_reason = choice.reason
            return {"status": "waiting", "reason": choice.reason, "run": self._save(run, coord)}
        if choice.action == "link_quote":
            qid, linked = choice.args["quote_item_id"], choice.args["item_ids"]
            if qid not in unlinked or len(linked) != len(set(linked)) or not set(linked) <= scope_ids:
                raise InvalidStateTransition("Invalid quote targets", run_id=run_id)
            if linked:
                coord.quote_links[qid] = linked
                coord.quote_source_result_id = run.state.agents["agent1"].current_result_id
            else:
                if len(coord.questions) >= 4:
                    coord.hold_reason = "추가 질문 한도에 도달했습니다. 견적 대상을 확인해 주세요."
                    return {"status": "waiting", "run": self._save(run, coord)}
                # 선택지는 최대 3개(+기타). 대상이 더 많으면 직접 입력으로 받는다.
                coord.questions.append(PMQuestion(question_id=f"quote-{qid}", item_ids=[qid],
                    field="quote_target", text="어떤 요청 항목의 견적인가요?",
                    reason="견적 요청이 어느 변경 요청에 대한 것인지 알아야 산정 대상을 정할 수 있습니다.",
                    choices=sorted(scope_ids) if len(scope_ids) <= MAX_QUESTION_CHOICES else []))
            return {"status": "ready" if linked else "waiting_input", "action": "link_quote", "run": self._save(run, coord)}
        if choice.action == "analyze_request":
            worker = lambda: self._analyze(inp)
        elif choice.action == "assess_scope":
            if a2_current:
                for item in choice.args["item_ids"]:
                    key = self._research_key(run, item)
                    coord.research_attempts[key] = coord.research_attempts.get(key, 0) + 1
                run = self._save(run, coord)
            worker = lambda: self._scope(run, inp, a1, a2, items, constraints, choice)
        else:
            worker = lambda: self._impact(run, inp, original.rate_card_id, a1, a2, a3, items, constraints, choice)
        return self._execute(run, choice.action, worker, counted=True)

    def _execute(self, run, action, worker, *, counted=False):
        if not counted:
            coord = run.coordination.model_copy(deep=True)
            coord.segment_steps += 1
            run = self._save(run, coord)
        stage = {"analyze_request": "agent1", "assess_scope": "agent2"}.get(action, "agent3")
        from app.orchestration.progress import record_activity
        activity = {"analyze_request": ("Agent 1", "고객 요청을 읽고 항목으로 정리하고 있습니다"),
                    "assess_scope": ("Agent 2", "요청 범위와 계약 근거를 검토하고 있습니다"),
                    "estimate_impact": ("Agent 3", "작업과 공수를 산정하고 있습니다"),
                    "draft_documents": ("Agent 3", "고객에게 전달할 문서 초안을 작성하고 있습니다")}
        record_activity(*activity[action])
        receipt = self.runner.execute(run.state.run_id, stage, worker, expected_revision=run.state.state_revision)
        return {"status": "ready", "action": action, "receipt": receipt,
                "run": self.state.get_coordinated_run(run.state.run_id)}

    def _scope(self, run, inp, a1, a2, items, constraints, choice):
        previous = _as(Agent2Output, a2.model_dump()) if a2 else None
        if previous:
            valid = {i.item_id for i in items if i.intent in SCOPE_INTENTS}
            previous.assessments = [a for a in previous.assessments if a.item_id in valid]
            previous.questions = [q for q in previous.questions if set(q.item_ids) <= valid]
        snap = Agent2CaseSnapshot(state_revision=0, context=inp.context, message_text=inp.message_text,
            a1_result_id=run.state.agents["agent1"].current_result_id, items=items, constraints=constraints,
            answers=inp.answers, a2_result=previous,
            a2_item_hashes=a2.input_item_hashes if a2 else {})
        local = SharedToolState(snap, inp, self.state.get_inputs(run.state.run_id).rate_card_id)
        result = Agent2OrchestratorAdapter(local.port("agent2"), lambda _: self.agents.agent2).execute(
            inp.context.case_id, 0, uuid4().hex, choice.model_dump())
        if result.status == "error":
            raise InvalidStateTransition(result.error.message if result.error else "Agent 2 tool failed")
        return Agent2RunOutput(**local.a2.a2_result.model_dump(), input_item_hashes=local.a2.a2_item_hashes)

    def _analyze(self, inp):
        output = self.agents.agent1.run(inp)
        if output.error:
            raise AgentFailure(output.error)
        if output.context != inp.context:
            raise InvalidStateTransition("Agent 1 returned a different request context")
        return output

    def _agent3_input(self, inp, a1, a2, rate, links, a1_id, calculation_context=None):
        items = [i for i in a1.items if i.item_type not in ("constraint", "deadline", "dependency")]
        constraints = [i for i in a1.items if i not in items]
        result = Agent3CaseSnapshot(state_revision=0, a1_input=inp, a1_result_id=a1_id,
            items=items, constraints=constraints, a2_result=_as(Agent2Output, a2.model_dump()),
            rate_card_id=rate, answers=inp.answers, quote_links=links).input()
        if calculation_context is not None:
            result.workbook_source_pinned = True
            if calculation_context.workbook_document_version_id:
                result.workbook_source = WorkbookReference(
                    document_version_id=calculation_context.workbook_document_version_id,
                    sha256=calculation_context.workbook_sha256)
        return result

    def _impact(self, run, inp, rate, a1, a2, old, items, constraints, choice):
        tools = Agent3Orchestration(self.agents.agent3, draft_llm=self.draft_llm)
        run_inputs = self.state.get_inputs(run.state.run_id)
        current = self._agent3_input(inp, a1, a2, rate, run.coordination.quote_links,
                                     run.state.agents["agent1"].current_result_id, run_inputs.calculation_context)
        view, previous = None, None
        context_changed = False
        if old and old.source:
            source = old.source
            previous_a1 = _as(Agent1Output, self.state.get_result(run.state.run_id, source.a1_result_id).result_payload)
            previous_a2 = _as(Agent2RunOutput, self.state.get_result(run.state.run_id, source.a2_result_id).result_payload)
            previous_inp = inp.model_copy(update={"answers": [a for a in inp.answers if a.answer_id in source.answer_ids]}, deep=True)
            previous = self._agent3_input(previous_inp, previous_a1, previous_a2, source.rate_card_id,
                                          source.quote_links, source.a1_result_id)
            previous.workbook_source_pinned = source.workbook_source_pinned
            previous.workbook_source = source.workbook_source
            context_changed = (source.rate_card_id != rate
                or source.calculation_context_revision != run_inputs.calculation_context_revision
                or previous.workbook_source_pinned != current.workbook_source_pinned
                or previous.workbook_source != current.workbook_source)
            if not context_changed and source.rate_hash == _rate_hash(rate) and source.effort_hash == _hash(load_effort_rules()):
                view = tools.restore_result(previous, _as(ProvisionalAgent3Output, old.model_dump()).model_dump(),
                    {k: d.model_dump(mode="json") for k, d in old.documents.items()},
                    rate_hash=source.rate_hash, effort_hash=source.effort_hash)
        if choice.action == "estimate_impact":
            selected = set(choice.args["item_ids"])
            if context_changed:
                selected = set(current.target_item_ids)
            if not view or selected == set(current.target_item_ids):
                if selected != set(current.target_item_ids):
                    raise InvalidStateTransition("First estimation requires all targets")
                view = tools.estimate_impact(current, previous_pricing=old.pricing if old else None)
            else:
                view = tools.reestimate(view["session_id"], Reestimate(state_revision=view["state_revision"],
                    input=current, item_ids=choice.args["item_ids"]))
        else:
            if view and previous != current:
                raise InvalidStateTransition("Re-estimate changed inputs before drafting")
            if not view:
                if current.target_item_ids:
                    raise InvalidStateTransition("Current impact required before drafting")
                view = tools.estimate_impact(current)
            view = tools.draft_documents(view["session_id"], DraftDocuments(state_revision=view["state_revision"],
                kind="bundle" if view["impact"]["estimates"] else "scope_reply"))
        raw = dict(view["impact"])
        raw["needs"], raw["documents"] = view["needs"], view["documents"]
        if any(n["blocking"] for n in view["needs"]):
            raw["status"] = "NEEDS_INPUT"
        raw["source"] = Agent3Source(a1_result_id=run.state.agents["agent1"].current_result_id,
            a2_result_id=run.state.agents["agent2"].current_result_id, answer_ids=[a.answer_id for a in inp.answers],
            rate_card_id=rate, quote_links=run.coordination.quote_links, rate_hash=_rate_hash(rate),
            effort_hash=_hash(load_effort_rules()), policy_hash=(raw.get("pricing") or {}).get("policy_hash"),
            pm_edit_id=old.source.pm_edit_id if old and old.source else None,
            calculation_context_revision=run_inputs.calculation_context_revision,
            workbook_source_pinned=current.workbook_source_pinned,
            workbook_source=current.workbook_source).model_dump()
        return Agent3RunOutput.model_validate(raw)

    def answer(self, request: AnswerQuestion):
        return self.state.answer_question(request)

    def run(self, run_id, *, expected_revision=None):
        """Continue automatically until a real question/review/hold or the step cap."""
        events = []
        for index in range(13):
            outcome = self.step(run_id, expected_revision=expected_revision if index == 0 else None)
            if outcome.get("action"):
                events.append({"action": outcome["action"], "status": outcome["status"]})
            if outcome["status"] != "ready":
                return {**outcome, "events": events}
        raise InvalidStateTransition("Supervisor failed to stop at its execution limit", run_id=run_id)

    @staticmethod
    def _validate_review_pricing(output):
        try:
            validate_output_pricing(output, require_confirmed=settings.calc_require_confirmed_policy)
        except (ToolInputError, BackendCalcError) as err:
            raise InvalidStateTransition(str(err)) from err

    def review(self, request: FinalReviewDecision):
        run = self.state.get_coordinated_run(request.run_id)
        output = self._result(run, "agent3", Agent3RunOutput)
        if request.decision == "approve" and output:
            self._validate_review_pricing(output)
        if output and output.source:
            original = self.state.get_inputs(request.run_id)
            if (output.source.rate_card_id != original.rate_card_id
                    or output.source.calculation_context_revision != original.calculation_context_revision
                    or output.source.rate_hash != _rate_hash(output.source.rate_card_id)
                    or output.source.effort_hash != _hash(load_effort_rules())):
                raise InvalidStateTransition("산정 기준이 변경되었습니다. 재산정 후 검토해 주세요.", run_id=request.run_id)
        return self.state.final_review(request)
