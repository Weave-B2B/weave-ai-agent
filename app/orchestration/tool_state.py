"""In-memory shared host for Agent 2 and Agent 3 tool adapters.

This host is independent of the legacy PM-review engine. It is a prototype,
not durable storage or a backend identifier adapter.
"""
from copy import deepcopy
from threading import RLock

from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot
from app.agents.agent3.orchestrator_adapter import Agent3CaseSnapshot
from app.agents.agent3.workflow import WorkflowConflict, _hash


def _substantive_a2_digest(a2_result) -> str | None:
    """Hash scope judgments and questions while ignoring run-specific metadata."""
    if a2_result is None:
        return None
    payload = {
        "status": a2_result.status,
        "assessments": [a.model_dump(mode="json") for a in a2_result.assessments],
        "questions": [q.model_dump(mode="json") for q in a2_result.questions],
    }
    return _hash(payload)


class SharedToolState:
    def __init__(self, snapshot: Agent2CaseSnapshot, a1_input, rate_card_id: str):
        if snapshot.context != a1_input.context or not snapshot.a1_result_id:
            raise ValueError("Agent 1 context and result ID must match")
        self.lock = RLock()
        self.a2 = snapshot.model_copy(deep=True)
        self.a1_input = a1_input.model_copy(deep=True)
        self.rate_card_id = rate_card_id
        self.a3_ref = None
        self.a3_view = None
        self.pending_question = None
        self.hold_reason = None
        self.last_needs = []  # Active needs from all agents, keyed by source_tool.
        self.archived_needs = []
        self.answers_pending_review = False
        self.a1_refresh_required = False
        self.a1_answer_question_id = None
        self.a3_stale = False
        self.final_review = None
        self.quote_links = {}
        self.reassess_counts = {}
        self.a2_no_progress = False
        self.receipts = {"agent2": {}, "agent3": {}}

    def replace_needs(self, source_tool: str, needs: list[dict]) -> None:
        """Replace only one worker's needs; never discard other workers' questions."""
        previous = [n for n in self.last_needs if n.get("source_tool") == source_tool]
        self.archived_needs.extend(deepcopy(previous))
        self.last_needs = [n for n in self.last_needs if n.get("source_tool") != source_tool]
        self.last_needs.extend([{**deepcopy(n), "source_tool": source_tool} for n in needs])

    def invalidate_downstream(self, *, keep_agent3_needs: bool = False) -> None:
        """A changed upstream result must not be displayed or approved as current."""
        if self.a3_view is not None:
            self.a3_stale = True
        if not keep_agent3_needs:
            if self.pending_question and self.pending_question.get("source_tool") == "agent3":
                self.pending_question = None
            self.replace_needs("agent3", [])
        self.final_review = None

    def port(self, stage):
        if stage not in self.receipts:
            raise ValueError("unknown agent")
        return _Port(self, stage)


class _Port:
    def __init__(self, host, stage):
        self.host, self.stage = host, stage

    def load(self, case_id):
        with self.host.lock:
            a2 = self.host.a2
            if a2.context.case_id != case_id:
                raise WorkflowConflict("case mismatch")
            if self.stage == "agent2":
                return a2.model_copy(deep=True)
            if a2.a2_result is None:
                raise WorkflowConflict("Agent 2 result required")
            return Agent3CaseSnapshot(state_revision=a2.state_revision, a1_input=self.host.a1_input,
                a1_result_id=a2.a1_result_id, items=a2.items, a2_result=a2.a2_result,
                rate_card_id=self.host.rate_card_id, constraints=a2.constraints, answers=a2.answers,
                execution_config_revision=a2.execution_config_revision, quote_links=self.host.quote_links,
                a3_ref=self.host.a3_ref).model_copy(deep=True)

    def read_operation(self, case_id, operation_id):
        with self.host.lock:
            if case_id != self.host.a2.context.case_id:
                raise WorkflowConflict("case mismatch")
            receipt = self.host.receipts[self.stage].get(operation_id)
            return receipt.model_copy(deep=True) if receipt else None

    def commit(self, case_id, expected_revision, update, operation_id, receipt):
        with self.host.lock:
            if case_id != self.host.a2.context.case_id or self.host.a2.state_revision != expected_revision:
                raise WorkflowConflict("shared state revision changed")
            if operation_id in self.host.receipts[self.stage]:
                raise WorkflowConflict("operation already recorded")
            if update is not None:
                if self.stage == "agent2":
                    prev_a2 = self.host.a2.a2_result
                    previous_id = prev_a2.meta.result_id if prev_a2 else None
                    prev_assessed = {a.item_id for a in prev_a2.assessments} if prev_a2 else set()
                    substantive_changed = _substantive_a2_digest(prev_a2) != _substantive_a2_digest(update.a2_result)
                    processed = list(receipt.result.processed_item_ids) if receipt and receipt.result else []
                    if not processed and update.a2_result.meta.debug:
                        processed = list(update.a2_result.meta.debug.get("processed_item_ids", []))
                    if not self.host.answers_pending_review:
                        for item_id in processed:
                            if item_id in prev_assessed:
                                self.host.reassess_counts[item_id] = self.host.reassess_counts.get(item_id, 0) + 1
                    self.host.a2_no_progress = bool(prev_a2 is not None and processed and not substantive_changed)
                    has_blocking_a3_needs = any(
                        n.get("source_tool") == "agent3" and n.get("blocking") and not n.get("answered")
                        for n in self.host.last_needs
                    )
                    self.host.a2.a2_result = update.a2_result.model_copy(deep=True)
                    self.host.a2.a2_item_hashes = dict(update.a2_item_hashes)
                    if previous_id != update.a2_result.meta.result_id:
                        if not substantive_changed and has_blocking_a3_needs:
                            self.host.final_review = None
                        else:
                            self.host.invalidate_downstream(keep_agent3_needs=not substantive_changed)
                else:
                    self.host.a3_ref = update.a3_ref.model_copy(deep=True)
                    self.host.a3_view = deepcopy(update.view)
                    self.host.rate_card_id = update.rate_card_id
                    self.host.a3_stale = False
                    self.host.a2_no_progress = False
                    self.host.final_review = None
                self.host.a2.state_revision += 1
            self.host.receipts[self.stage][operation_id] = receipt.model_copy(deep=True)
