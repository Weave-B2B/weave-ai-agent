"""Backend-backed Run store connecting prototype routes to the real Spring Boot Backend.

Delegates state and coordination operations to HttpCoordinatedRunStateClient while caching
local UI progress events in SQLite.
"""
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from app.orchestration.http_run_coordination import HttpCoordinatedRunStateClient
from app.orchestration.local_run_store import LocalRunStore
from app.orchestration.run_state import RunNotFound


def to_uuid_str(val: str) -> str:
    try:
        return str(uuid.UUID(val))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, val))


class BackendRunStore:
    def __init__(self, base_url: str, token: str, actor_member_id: str,
                 local_db_path: Path, project_id: str = "00000000-0000-0000-0000-0000000000a1"):
        self.client = HttpCoordinatedRunStateClient(base_url, token, actor_member_id, coordination_enabled=True)
        self.local_store = LocalRunStore(local_db_path)
        self.project_id = project_id
        self.path = Path(local_db_path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS backend_run_meta (case_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, provider TEXT NOT NULL, history TEXT NOT NULL DEFAULT '[]')")

    @property
    def is_backend_connected(self) -> bool:
        return True

    def _save_meta(self, case_id: str, run_id: str, provider: str):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "INSERT INTO backend_run_meta (case_id, run_id, provider, history) VALUES (?, ?, ?, '[]') "
                "ON CONFLICT(case_id) DO UPDATE SET run_id=excluded.run_id, provider=excluded.provider",
                (case_id, run_id, provider)
            )

    def _append_history(self, case_id: str, event: dict):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            row = db.execute("SELECT history, run_id, provider FROM backend_run_meta WHERE case_id=?", (case_id,)).fetchone()
            if row:
                history = json.loads(row[0])
                history.append(event)
                db.execute("UPDATE backend_run_meta SET history=? WHERE case_id=?", (json.dumps(history, ensure_ascii=False), case_id))
            else:
                db.execute("INSERT INTO backend_run_meta VALUES (?, ?, 'gpt', ?)",
                           (case_id, to_uuid_str(case_id), json.dumps([event], ensure_ascii=False)))

    def provider(self, case_id: str) -> str:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT provider FROM backend_run_meta WHERE case_id=?", (case_id,)).fetchone()
        return row[0] if row else "gpt"

    def progress(self, case_id: str):
        return self.local_store.progress(case_id)

    def save_progress(self, case_id: str, events):
        self.local_store.save_progress(case_id, events)

    def history(self, case_id: str):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT history FROM backend_run_meta WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def to_backend_run_id(self, case_id: str) -> str:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT run_id FROM backend_run_meta WHERE case_id=?", (case_id,)).fetchone()
        if row:
            return row[0]
        return to_uuid_str(case_id)

    def create_run(self, case_id: str, inputs, *, provider: str = "gpt"):
        cr_id = to_uuid_str(case_id)
        state = self.client.create_run(self.project_id, cr_id, inputs)
        run_id = state.run_id
        self._save_meta(case_id, run_id, provider)
        self._append_history(case_id, {"event": "run_created", "run_id": run_id, "case_id": case_id})
        return state

    def get_state(self, case_id: str):
        return self.client.get_state(self.to_backend_run_id(case_id))

    def get_inputs(self, case_id: str):
        return self.client.get_inputs(self.to_backend_run_id(case_id))

    def get_result(self, case_id: str, result_id: str):
        return self.client.get_result(self.to_backend_run_id(case_id), result_id)

    def get_coordinated_run(self, case_id: str):
        return self.client.get_coordinated_run(self.to_backend_run_id(case_id))

    def acquire_execution(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.acquire_execution(c_req)
        self._append_history(req.run_id, {"event": "execution_acquired", "agent_type": req.agent_type, "operation_id": req.operation_id})
        return receipt

    def commit_result(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.commit_result(c_req)
        self._append_history(req.run_id, {"event": "result_committed", "agent_type": req.agent_type, "result_id": req.result_id, "operation_id": req.operation_id})
        return receipt

    def finish_unchanged(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.finish_unchanged(c_req)
        self._append_history(req.run_id, {"event": "execution_unchanged", "agent_type": req.agent_type, "result_id": req.target_result_id, "operation_id": req.operation_id})
        return receipt

    def save_coordination(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.save_coordination(c_req)
        self._append_history(req.run_id, {"event": "coordination_saved", "operation_id": req.operation_id})
        return receipt

    def answer_question(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.answer_question(c_req)
        self._append_history(req.run_id, {"event": "question_answered", "question_id": req.question_id, "answer": req.answer, "operation_id": req.operation_id})
        return receipt

    def request_final_review(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.request_final_review(c_req)
        self._append_history(req.run_id, {"event": "final_review_requested", "operation_id": req.operation_id})
        return receipt

    def final_review(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.final_review(c_req)
        self._append_history(req.run_id, {"event": "final_review", "decision": req.decision, "bundle": req.bundle.model_dump(), "operation_id": req.operation_id})
        return receipt

    def reserve_llm_attempt(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        return self.client.reserve_llm_attempt(c_req)

    def resume_run(self, req):
        c_req = req.model_copy(update={"run_id": self.to_backend_run_id(req.run_id)})
        receipt = self.client.resume_run(c_req)
        self._append_history(req.run_id, {"event": "run_resumed", "resumed_by": req.resumed_by, "operation_id": req.operation_id})
        return receipt
