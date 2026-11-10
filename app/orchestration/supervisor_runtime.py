"""Local bounded supervisor runner for the Agent 2/3 tool ports."""
from uuid import uuid4
from datetime import datetime, timezone
from copy import deepcopy

from app.orchestration.decision import choose_action, InvalidDecision
from app.orchestration.progress import record_activity
from app.agents.agent2.orchestrator_adapter import SCOPE_INTENTS
from schema.schemas import Answer, AnswerAnalysisInput
from app.agents.agent3.workflow import WorkflowConflict


_ACTION_ACTIVITY = {
    "analyze_request": ("Agent 1", "요청 항목을 다시 분석하고 있습니다"),
    "link_quote": ("총괄", "견적 문의를 대상 항목에 연결하고 있습니다"),
    "assess_scope": ("Agent 2", "요청 범위와 계약 근거를 검토하고 있습니다"),
    "estimate_impact": ("Agent 3", "변경에 필요한 작업과 공수를 산정하고 있습니다"),
    "draft_documents": ("Agent 3", "고객에게 전달할 문서 초안을 작성하고 있습니다"),
}

MAX_REASSESS_PER_ITEM = 1


class SupervisorRuntime:
    def __init__(self, state, agent2, agent3, llm, *, agent1=None):
        self.state, self.agent2, self.agent3, self.llm = state, agent2, agent3, llm
        self.agent1 = agent1
        self.history = []

    def _reconcile_state(self):
        """Promote optional PM questions and cap futile re-searches when items are stuck."""
        a2 = self.state.a2
        answered_qids = {a.question_id for a in a2.answers} | {
            n.get("question_id") for n in self.state.last_needs + self.state.archived_needs if n.get("answered")
        }
        existing_qids = {n.get("question_id") for n in self.state.last_needs if n.get("question_id")}
        a1_result = getattr(self.state, "a1_result", None)
        if a1_result:
            for q in a1_result.questions:
                if q.status == "open" and q.question_id not in existing_qids and q.question_id not in answered_qids:
                    self.state.last_needs.append({
                        **q.model_dump(mode="json"),
                        "need_id": f"a1-need-{q.question_id}",
                        "kind": "client_input",
                        "topic": q.text,
                        "source_tool": "agent1",
                    })
                    existing_qids.add(q.question_id)
        if a2.a2_result:
            for q in a2.a2_result.questions:
                if q.status == "open" and q.question_id not in existing_qids and q.question_id not in answered_qids:
                    split = q.field == "item_split"
                    self.state.last_needs.append({
                        "need_id": f"a2-need-{q.question_id}",
                        "kind": "resplit" if split else "client_input",
                        "item_ids": list(q.item_ids),
                        "topic": q.text,
                        "target": "agent1" if split else q.target,
                        "source": "agent2",
                        "source_tool": "agent2",
                        "question_id": q.question_id,
                        "blocking": q.blocking,
                    })
                    existing_qids.add(q.question_id)

        active_a3_needs = [
            n for n in self.state.last_needs
            if n.get("source_tool") == "agent3"
            and not n.get("answered")
            and (not self.state.a3_stale or self.state.a2_no_progress)
            and n.get("kind") in ("more_evidence", "client_input", "missing_standard")
        ]
        a3_stuck_items = {i for n in active_a3_needs for i in n.get("item_ids", [])}
        exhausted_items = {
            i for i, count in self.state.reassess_counts.items()
            if count >= MAX_REASSESS_PER_ITEM
        }
        unclear_items = (
            {a.item_id for a in a2.a2_result.assessments if a.label == "unclear"}
            if a2.a2_result else set()
        )
        stuck_items = a3_stuck_items | exhausted_items | unclear_items

        if stuck_items:
            for n in self.state.last_needs:
                if (not n.get("answered")
                        and n.get("question_id")
                        and n.get("question_id") not in answered_qids
                        and n.get("kind") != "resplit"
                        and set(n.get("item_ids", [])) & stuck_items):
                    n["blocking"] = True
                    n["target"] = "pm"
                    if n.get("source_tool") == "agent1" and a2.a2_result is not None:
                        n["source_tool"] = "agent2"
                        n["stage"] = "agent2"

        pm_blocked_items = {
            i for n in self.state.last_needs
            if n.get("blocking") and not n.get("answered") and n.get("question_id") and n.get("question_id") not in answered_qids and n.get("target") == "pm"
            for i in n.get("item_ids", [])
        }
        already_synth_items = {
            i for n in self.state.last_needs + self.state.archived_needs
            if (n.get("answered") or n.get("question_id") in answered_qids)
            for i in n.get("item_ids", [])
        }
        if already_synth_items:
            self.state.last_needs = [
                n for n in self.state.last_needs
                if not (
                    n.get("source_tool") == "agent3"
                    and n.get("kind") == "more_evidence"
                    and set(n.get("item_ids", [])) <= already_synth_items
                )
            ]
        hard_stuck_items = ((a3_stuck_items & (unclear_items | exhausted_items)) | exhausted_items) - pm_blocked_items - already_synth_items
        if hard_stuck_items and a2.a2_result is not None:
            assessments_by_id = {a.item_id: a for a in a2.a2_result.assessments}
            for item_id in sorted(hard_stuck_items):
                synth_qid = f"{item_id}-A2Q-pm-{len(a2.answers)}"
                if synth_qid in answered_qids or synth_qid in existing_qids:
                    continue
                assessment = assessments_by_id.get(item_id)
                topic = None
                if assessment and assessment.missing_info:
                    topic = f"{assessment.missing_info[0]}에 대한 확인이 필요합니다."
                if not topic:
                    for n in active_a3_needs:
                        if item_id in n.get("item_ids", []) and n.get("topic"):
                            topic = n["topic"]
                            break
                if topic:
                    self.state.last_needs.append({
                        "need_id": f"synth-pm-{synth_qid}",
                        "kind": "client_input",
                        "item_ids": [item_id],
                        "topic": topic,
                        "target": "pm",
                        "source": "agent2",
                        "source_tool": "agent2",
                        "question_id": synth_qid,
                        "blocking": True,
                    })
                    pm_blocked_items.add(item_id)

        if pm_blocked_items:
            self.state.last_needs = [
                n for n in self.state.last_needs
                if not (n.get("kind") == "more_evidence" and set(n.get("item_ids", [])) <= pm_blocked_items)
            ]

        if exhausted_items:
            for n in self.state.last_needs:
                if (not n.get("answered")
                        and n.get("kind") == "more_evidence"
                        and set(n.get("item_ids", [])) <= exhausted_items):
                    n["blocking"] = True

    def step(self):
        """Choose and execute one action, returning control to the supervisor."""
        with self.state.lock:
            if self.state.pending_question:
                return {"status": "waiting_input", "question": dict(self.state.pending_question)}
            if self.state.final_review and self.state.final_review.get("decision") == "approve":
                return {"status": "completed", "review": dict(self.state.final_review)}
            if self.state.hold_reason:
                return {"status": "waiting", "reason": self.state.hold_reason}
            self._reconcile_state()
            snap = self.state.a2.model_copy(deep=True)
            view = deepcopy(self.state.a3_view) if self.state.a3_view else None
            stale = self.state.a3_stale
            no_progress = self.state.a2_no_progress
            reassess_counts = dict(self.state.reassess_counts)
            needs = deepcopy(self.state.last_needs)
            answer_pending = self.state.answers_pending_review
            a1_refresh = self.state.a1_refresh_required
            a1_question_id = self.state.a1_answer_question_id
            quote_links = deepcopy(self.state.quote_links)
        ids = {i.item_id for i in snap.items}
        scope_item_ids = {i.item_id for i in snap.items if i.intent in SCOPE_INTENTS}
        assessed_ids = {a.item_id for a in snap.a2_result.assessments} if snap.a2_result else set()
        active_needs = [n for n in needs if not (stale and not no_progress and n.get("source_tool") == "agent3")]
        answered_qids = {a.question_id for a in snap.answers} | {
            n.get("question_id") for n in needs + self.state.archived_needs if n.get("answered")
        }
        blocking = [n for n in active_needs if n.get("blocking") and not n.get("answered") and n.get("question_id") not in answered_qids]
        questions = {n["question_id"]: n for n in blocking if n.get("question_id") and n.get("target") == "pm" and not n.get("answered") and n.get("question_id") not in answered_qids}
        pm_blocked_items = {
            i for n in blocking
            if n.get("question_id") and n.get("target") == "pm"
            for i in n.get("item_ids", [])
        }
        eligible_scope_ids = {
            i for i in scope_item_ids
            if i not in pm_blocked_items
            and (i not in assessed_ids or answer_pending or reassess_counts.get(i, 0) < MAX_REASSESS_PER_ITEM)
        }
        estimate_target_ids = {
            i.item_id for i in snap.items
            if i.intent == "change_request" or (i.intent == "confirmation" and i.conditional_followup)
        }
        estimate_target_ids.update(i for target_list in quote_links.values() for i in target_list)
        if not ids and not a1_refresh:
            return {"status": "waiting", "reason": "분석할 항목이 없습니다."}
        if view and not stale and view.get("can_final_review") and not blocking and not answer_pending:
            return {"status": "waiting_final_review", "snapshot_hash": view["snapshot_hash"]}
        if a1_refresh:
            allowed = {"analyze_request", "hold"} if self.agent1 else {"hold"}
        else:
            allowed = {"hold"}
            if eligible_scope_ids:
                allowed.add("assess_scope")
            quote_ids = {i.item_id for i in snap.items if i.intent == "estimate_request"}
            unlinked_quotes = quote_ids - quote_links.keys()
            if unlinked_quotes:
                allowed.add("link_quote")
            if questions:
                allowed.add("ask_pm")
            if any(n.get("kind") == "resplit" for n in active_needs) and self.agent1:
                allowed.add("analyze_request")
            if snap.a2_result is not None and scope_item_ids <= assessed_ids and not unlinked_quotes and not blocking and not answer_pending:
                has_estimate_target = any(i.intent == "change_request" or (i.intent == "confirmation" and i.conditional_followup)
                                          for i in snap.items) or bool(quote_links)
                if has_estimate_target:
                    allowed.add("estimate_impact")
                if (not has_estimate_target or (view and not stale)) and not answer_pending:
                    allowed.add("draft_documents")
        last_worker_error = self.history[-1].get("error") if self.history and self.history[-1].get("status") == "error" else None
        summary = {"items": [{"item_id": i.item_id, "text": i.text, "intent": i.intent} for i in snap.items],
                   "scope": snap.a2_result.model_dump(mode="json") if snap.a2_result else None,
                   "impact": view.get("impact") if view and not stale else None,
                   "impact_stale": stale, "needs": active_needs,
                   "reassess_counts": reassess_counts,
                   "last_worker_error": last_worker_error,
                   "message_text": self.state.a1_input.message_text, "quote_links": quote_links,
                   "answers": [a.model_dump(mode="json") for a in snap.answers],
                   "answer_pending_review": answer_pending, "a1_refresh_required": a1_refresh,
                   "a1_answer_question_id": a1_question_id, "history": self.history[-6:]}
        try:
            choice = choose_action(self.llm, state_summary=summary, allowed_actions=allowed,
                                   item_ids=ids, question_ids=set(questions),
                                   scope_item_ids=eligible_scope_ids, assessed_item_ids=assessed_ids,
                                   estimate_item_ids=estimate_target_ids)
        except InvalidDecision as exc:
            return {"status": "error", "reason": str(exc)}
        if choice.action in _ACTION_ACTIVITY:
            record_activity(*_ACTION_ACTIVITY[choice.action])
        if choice.action == "analyze_request":
            return self._reanalyze(choice, snap, active_needs)
        case_id = snap.context.case_id
        if choice.action == "link_quote":
            quote_id, targets = choice.args["quote_item_id"], choice.args["item_ids"]
            if quote_id not in unlinked_quotes or len(targets) != len(set(targets)) or not set(targets) <= scope_item_ids:
                return {"status": "error", "reason": "견적 문의와 대상 항목을 확인해주세요."}
            with self.state.lock:
                if self.state.a2.state_revision != snap.state_revision:
                    return {"status": "error", "reason": "상태가 변경되어 다시 확인해야 합니다."}
                self.state.a2.state_revision += 1
                if not targets:
                    self.state.pending_question = {"question_id": f"quote-{quote_id}", "topic": "어떤 요청 항목의 견적인가요?", "item_ids": [quote_id], "target": "pm", "blocking": True}
                    return {"status": "waiting_input", "action": "link_quote", "question": dict(self.state.pending_question), "state_revision": self.state.a2.state_revision}
                self.state.quote_links[quote_id] = targets
                self.state.invalidate_downstream()
                event = {"action": "link_quote", "reason": choice.reason, "status": "ready", "quote_item_id": quote_id, "item_ids": targets}
                self.history.append(event)
                return event
        if choice.action in ("ask_pm", "hold"):
            with self.state.lock:
                if self.state.a2.state_revision != snap.state_revision:
                    return {"status": "error", "reason": "상태가 변경되어 다시 확인해야 합니다."}
                if choice.action == "ask_pm":
                    self.state.pending_question = questions[choice.args["question_id"]]
                    self.state.a2.state_revision += 1
                    return {"status": "waiting_input", "action": "ask_pm", "question": dict(self.state.pending_question), "state_revision": self.state.a2.state_revision}
                self.state.hold_reason = choice.reason
                self.state.a2.state_revision += 1
                return {"status": "waiting", "action": "hold", "reason": choice.reason, "state_revision": self.state.a2.state_revision}
        adapter = self.agent2 if choice.action == "assess_scope" else self.agent3
        result = adapter.execute(case_id, snap.state_revision, str(uuid4()), choice.model_dump())
        event = {"action": choice.action, "reason": choice.reason, "status": result.status,
                 "result_ids": result.result_ids, "needs": [n.model_dump() if hasattr(n, "model_dump") else n for n in result.needs]}
        event["error"] = result.error.model_dump() if result.error else None
        self.history.append(event)
        if result.status != "error":
            with self.state.lock:
                source = "agent2" if choice.action == "assess_scope" else "agent3"
                self.state.replace_needs(source, event["needs"])
                self.state.answers_pending_review = False
                self._reconcile_state()
        return event

    def _reanalyze(self, choice, snap, needs):
        """Re-run Agent 1 after a PM answer or an Agent 2 resplit request."""
        from app.orchestration.ids import new_result_id
        mode = choice.args["mode"]
        qid = choice.args.get("question_id")
        if mode == "answer":
            if not self.state.a1_refresh_required or qid != self.state.a1_answer_question_id:
                return {"status": "error", "reason": "해석할 Agent 1 답변이 없습니다."}
        elif mode != "resplit" or not any(n.get("kind") == "resplit" for n in needs):
            return {"status": "error", "reason": "재분리 요청이 없습니다."}
        try:
            inp = self.state.a1_input.model_copy(update={"answers": list(snap.answers)})
            result = self.agent1.run(inp)
            if result.context != inp.context or result.status == "ERROR":
                raise ValueError("Agent 1 재분석 결과가 유효하지 않습니다.")
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}
        result_id = new_result_id("agent1", inp.context.case_id)
        needs_a1 = [{**q.model_dump(mode="json"), "kind": "client_input", "topic": q.text, "source_tool": "agent1"}
                    for q in result.questions if q.status == "open"]
        blocking_a1 = [n for n in needs_a1 if n.get("blocking")]
        with self.state.lock:
            if self.state.a2.state_revision != snap.state_revision:
                return {"status": "error", "reason": "분석 중 상태가 변경됐습니다."}
            self.state.a1_result = result.model_copy(deep=True)
            self.state.a1_result.meta.result_id = result_id
            self.state.a2.a1_result_id = result_id
            self.state.a2.items = [i for i in result.items if i.item_type not in ("constraint", "deadline", "dependency")]
            self.state.a2.constraints = [i for i in result.items if i.item_type in ("constraint", "deadline", "dependency")]
            self.state.a2.a2_result = None
            self.state.a2.a2_item_hashes = {}
            self.state.invalidate_downstream()
            if self.state.a3_view:
                self.state.archived_needs.append({"source_tool": "agent3", "kind": "stale_result", "result_id": self.state.a3_view["impact"]["meta"]["result_id"]})
            self.state.a3_ref = None
            self.state.a3_view = None
            self.state.a3_stale = False
            for stage in ("agent2", "agent3"):
                self.state.replace_needs(stage, [])
            self.state.replace_needs("agent1", needs_a1)
            self.state.pending_question = blocking_a1[0] if blocking_a1 else None
            self.state.a1_refresh_required = False
            self.state.a1_answer_question_id = None
            self.state.answers_pending_review = False
            self.state.quote_links.clear()
            self.state.reassess_counts.clear()
            self.state.a2_no_progress = False
            self.state.a2.state_revision += 1
        event = {"action": "analyze_request", "reason": choice.reason,
                 "status": "waiting_input" if blocking_a1 else "ready", "result_ids": [result_id]}
        self.history.append(event)
        return event

    def answer(self, question_id, text, state_revision):
        """Record a PM answer; it is not an automatic resolution or approval."""
        with self.state.lock:
            if self.state.a2.state_revision != state_revision:
                raise WorkflowConflict("stale state")
            q = self.state.pending_question
            if not q or q["question_id"] != question_id or not text.strip():
                raise ValueError("현재 질문과 답변을 확인해주세요.")
            answer = Answer(answer_id=str(uuid4()), question_id=question_id,
                text=text.strip(), answered_by="pm", answered_at=datetime.now(timezone.utc).isoformat())
            source = q.get("source_tool") or q.get("stage")
            resolved = None
            if source == "agent1":
                if self.agent1 is None:
                    raise ValueError("Agent 1 재분석 도구가 연결되지 않았습니다.")
                question = next((item for item in self.state.a1_result.questions if item.question_id == question_id), None)
                if question is None:
                    raise ValueError("Agent 1 질문 기록을 찾지 못했습니다.")
                record_activity("Agent 1", "PM 답변으로 요청이 명확해졌는지 확인하고 있습니다")
                result = self.agent1.analyze_answer(AnswerAnalysisInput(context=self.state.a1_input.context,
                    question=question, answer=answer))
                resolved = result.resolved
            self.state.a2.answers.append(answer)
            if source == "agent1" and not resolved:
                # An ambiguous answer must not silently unblock the analysis.
                self.state.pending_question = {**q, "reason": "답변이 불충분합니다. 추가 설명이 필요합니다."}
            else:
                self.state.pending_question = None
                self.state.answers_pending_review = True
                if source == "agent1":
                    self.state.a1_refresh_required = True
                    self.state.a1_answer_question_id = question_id
                if source in ("agent1", "agent2", "agent3"):
                    self.state.invalidate_downstream()
                for item_id in q.get("item_ids", []) or list(self.state.reassess_counts):
                    self.state.reassess_counts.pop(item_id, None)
                self.state.a2_no_progress = False
                for need in self.state.last_needs:
                    if need.get("question_id") == question_id:
                        need["answered"] = True
            self.state.a2.state_revision += 1
            return self.state.a2.state_revision

    def review(self, *, decision, state_revision, expected_snapshot_hash, reason="", acknowledge_unknown_schedule=False):
        """Prototype PM action, never exposed to the supervisor LLM."""
        if decision not in ("approve", "hold", "reject"):
            raise ValueError("지원하지 않는 최종 검토 결정입니다.")
        with self.state.lock:
            if self.state.a2.state_revision != state_revision:
                raise WorkflowConflict("stale state")
            view = self.state.a3_view
            if not view or self.state.a3_stale or view.get("snapshot_hash") != expected_snapshot_hash:
                raise WorkflowConflict("최신 산정·문서 스냅샷을 다시 확인해 주세요.")
            if self.state.pending_question or self.state.a1_refresh_required or self.state.answers_pending_review:
                raise ValueError("미처리 질문 또는 답변 재검증이 남아 있습니다.")
            if any(n.get("blocking") and not n.get("answered") for n in self.state.last_needs):
                raise ValueError("해결되지 않은 필수 질문이 있습니다.")
            if decision == "approve" and (not view.get("can_final_review") or not view.get("documents", {}).get("email")):
                raise ValueError("산정과 고객 문서 초안이 준비되어야 승인할 수 있습니다.")
            if decision != "approve" and not reason.strip():
                raise ValueError("보류 또는 반려 사유를 입력해 주세요.")
            case_id = self.state.a2.context.case_id
        result = self.agent3.execute_pm(case_id, state_revision, str(uuid4()),
            {"action": "review", "expected_snapshot_hash": expected_snapshot_hash,
             "decision": decision, "reason": reason,
             "acknowledge_unknown_schedule": acknowledge_unknown_schedule}, actor_ref="prototype-pm")
        if result.status == "error":
            raise ValueError(result.error.message if result.error else "최종 검토를 저장하지 못했습니다.")
        with self.state.lock:
            updated = self.state.a3_view
            review = updated.get("approval") if decision == "approve" else updated.get("last_review")
            if not review or review.get("decision") != decision:
                raise ValueError("최종 검토 결과가 저장되지 않았습니다.")
            self.state.final_review = deepcopy(review)
            self.state.hold_reason = reason if decision != "approve" else None
            self.history.append({"action": "final_review", "status": "completed" if decision == "approve" else "waiting",
                                 "reason": reason or "PM 최종 검토 승인", "review_id": review["review_id"]})
            return deepcopy(review)

    def resume(self, state_revision):
        with self.state.lock:
            if self.state.a2.state_revision != state_revision:
                raise WorkflowConflict("stale state")
            if not self.state.hold_reason:
                raise ValueError("재개할 보류 상태가 없습니다.")
            self.state.hold_reason = None
            self.state.final_review = None
            self.state.reassess_counts.clear()
            self.state.a2_no_progress = False
            self.state.a2.state_revision += 1
            return self.state.a2.state_revision

    def run(self, max_steps=6):
        if max_steps < 1 or max_steps > 12:
            raise ValueError("max_steps must be between 1 and 12")
        events = []
        for _ in range(max_steps):
            result = self.step()
            events.append(result)
            if result["status"] in ("waiting", "waiting_input", "waiting_final_review", "completed", "error") or result.get("action") == "draft_documents":
                break
        else:
            with self.state.lock:
                ready = bool(self.state.a3_view and not self.state.a3_stale and self.state.a3_view.get("can_final_review"))
                if not ready and not self.state.pending_question and not self.state.hold_reason:
                    unresolved = [n.get("topic") for n in self.state.last_needs if not n.get("answered") and n.get("topic")]
                    reason = f"추가 확인 필요: {'; '.join(dict.fromkeys(unresolved))}" if unresolved else "최대 자동 실행 단계에 도달하여 검토 대기로 전환합니다."
                    self.state.hold_reason = reason[:300]
                    self.state.a2.state_revision += 1
                    hold_event = {"status": "waiting", "action": "hold", "reason": self.state.hold_reason, "state_revision": self.state.a2.state_revision}
                    self.history.append(hold_event)
                    events.append(hold_event)
        return events
