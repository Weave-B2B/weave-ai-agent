"""Opt-in checkpoints for the single-process local Supervisor prototype."""
import json
import sqlite3
from contextlib import closing
from functools import lru_cache

from app.agents.agent2.orchestrator_adapter import Agent2CaseSnapshot, OperationReceipt as Agent2Receipt
from app.agents.agent3.orchestrator_adapter import Agent3Reference, OperationReceipt as Agent3Receipt
from app.agents.agent3.workflow import WorkflowConflict
from app.orchestration.tool_state import SharedToolState
from schema.schemas import Agent1Input, Agent1Output


_HOST_FIELDS = (
    "a3_view", "pending_question", "hold_reason", "last_needs", "archived_needs",
    "answers_pending_review", "a1_refresh_required", "a1_answer_question_id", "a3_stale",
    "final_review", "quote_links", "reassess_counts", "a2_no_progress",
)
_RECEIPTS = {"agent2": Agent2Receipt, "agent3": Agent3Receipt}


def checkpoint(runtime, provider, progress):
    state = runtime.state
    # The caller holds the case lock, so the worker session and host state commit together.
    return {
        "format": 1,
        "provider": provider,
        "a1_input": state.a1_input.model_dump(mode="json"),
        "a1_result": state.a1_result.model_dump(mode="json"),
        "a2": state.a2.model_dump(mode="json"),
        "rate_card_id": state.rate_card_id,
        "a3_ref": state.a3_ref.model_dump(mode="json") if state.a3_ref else None,
        "a3_session": runtime.agent3.export_checkpoint(state.a2.context.case_id) if state.a3_ref else None,
        "host": {field: getattr(state, field) for field in _HOST_FIELDS},
        "receipts": {agent: {key: value.model_dump(mode="json") for key, value in receipts.items()}
                     for agent, receipts in state.receipts.items()},
        "history": runtime.history,
        "progress": progress.after(),
    }


def restore_host(case_id, payload):
    if payload["format"] != 1 or payload["provider"] not in ("gpt", "local"):
        raise ValueError("unsupported Supervisor checkpoint")
    a2 = Agent2CaseSnapshot.model_validate(payload["a2"])
    if a2.context.case_id != case_id:
        raise ValueError("Supervisor checkpoint belongs to another case")
    state = SharedToolState(a2, Agent1Input.model_validate(payload["a1_input"]), payload["rate_card_id"])
    state.a1_result = Agent1Output.model_validate(payload["a1_result"])
    for field in _HOST_FIELDS:
        if field in payload["host"]:
            setattr(state, field, payload["host"][field])
    if payload["a3_ref"] is not None:
        state.a3_ref = Agent3Reference.model_validate(payload["a3_ref"])
    if (state.a3_ref is None) != (payload["a3_session"] is None):
        raise ValueError("incomplete Agent 3 checkpoint")
    state.receipts = {agent: {key: model.model_validate(value) for key, value in payload["receipts"][agent].items()}
                      for agent, model in _RECEIPTS.items()}
    return state


class SupervisorStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS supervisor_case (
                case_id TEXT PRIMARY KEY, storage_revision INTEGER NOT NULL, checkpoint TEXT NOT NULL
            )""")

    def load(self, case_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT storage_revision, checkpoint FROM supervisor_case WHERE case_id = ?",
                             (case_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save(self, case_id, payload, expected_revision):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            if expected_revision is None:
                result = db.execute("INSERT INTO supervisor_case VALUES (?, 1, ?) ON CONFLICT DO NOTHING",
                                    (case_id, body))
            else:
                result = db.execute("UPDATE supervisor_case SET storage_revision = storage_revision + 1, checkpoint = ? "
                                    "WHERE case_id = ? AND storage_revision = ?", (body, case_id, expected_revision))
            if result.rowcount != 1:
                raise WorkflowConflict("Supervisor 상태가 변경되었습니다. 최신 상태를 다시 확인해 주세요.")
        return 1 if expected_revision is None else expected_revision + 1


@lru_cache(maxsize=1)
def local_store(path):
    return SupervisorStore(path) if path is not None else None
