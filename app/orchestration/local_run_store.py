"""SQLite-backed Run contract for explicitly enabled local UI testing.

Uses the strict reference transition rules. Never presented as Backend storage.
Every write reloads under BEGIN IMMEDIATE; LLM work happens outside transactions.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.orchestration.fake_run_coordination import FakeCoordinatedRunStateClient
from app.orchestration.run_coordination import CoordinationState, ExecutionLease, ExecutionReceipt, ReviewBundle
from app.orchestration.run_state import (
    AgentState, CommittedResult, InvalidStateTransition, PendingReview, RunInputs,
    RunNotFound, StateChangeReceipt,
)


class LocalRunStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.touch(exist_ok=True, mode=0o600)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS local_run (id TEXT PRIMARY KEY, provider TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS local_run_progress (id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    @staticmethod
    def _dump(run):
        return json.dumps({
            "revision": run.revision, "status": run.status,
            "agents": {k: v.model_dump(mode="json") for k, v in run.agents.items()},
            "result_counts": run.result_counts,
            "pending": run.pending.model_dump(mode="json") if run.pending else None,
            "history": run.history,
            "results": {k: v.model_dump(mode="json") for k, v in run.results.items()},
            "receipts": {k: [digest, receipt.model_dump(mode="json")] for k, (digest, receipt) in run.receipts.items()},
            "inputs": run.inputs.model_dump(mode="json") if run.inputs else None,
            "pm_review_revision": run.pm_review_revision,
            "coordination": run.coordination.model_dump(mode="json"),
            "executions": {k: v.model_dump(mode="json") for k, v in run.executions.items()},
            "review_bundle": run.review_bundle.model_dump(mode="json") if run.review_bundle else None,
        }, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _load(run_id, body):
        data = json.loads(body)
        client = FakeCoordinatedRunStateClient()
        client.create_run(run_id, RunInputs.model_validate(data["inputs"]) if data["inputs"] else None)
        run = client._runs[run_id]
        for key in ("revision", "status", "result_counts", "history", "pm_review_revision"):
            setattr(run, key, data[key])
        run.agents = {k: AgentState.model_validate(v) for k, v in data["agents"].items()}
        run.pending = PendingReview.model_validate(data["pending"]) if data["pending"] else None
        run.results = {k: CommittedResult.model_validate(v) for k, v in data["results"].items()}
        run.receipts = {k: (digest, (ExecutionReceipt if "execution_token" in receipt else StateChangeReceipt).model_validate(receipt))
                        for k, (digest, receipt) in data["receipts"].items()}
        run.coordination = CoordinationState.model_validate(data["coordination"])
        run.executions = {k: ExecutionLease.model_validate(v) for k, v in data["executions"].items()}
        run.review_bundle = ReviewBundle.model_validate(data["review_bundle"]) if data["review_bundle"] else None
        return client

    def create_run(self, run_id, inputs, *, provider="gpt"):
        client = FakeCoordinatedRunStateClient()
        client.create_run(run_id, inputs)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            cursor = db.execute("INSERT INTO local_run VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                                (run_id, provider, self._dump(client._runs[run_id])))
            if not cursor.rowcount:
                raise InvalidStateTransition("이미 등록된 로컬 Run입니다.", run_id=run_id)
        return self.get_state(run_id)

    def provider(self, run_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT provider FROM local_run WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise RunNotFound(run_id, run_id=run_id)
        return row[0]

    def progress(self, run_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT body FROM local_run_progress WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def save_progress(self, run_id, events):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO local_run_progress VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (run_id, json.dumps(events, ensure_ascii=False)))

    def _call(self, run_id, method, *args, write=False):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM local_run WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RunNotFound(run_id, run_id=run_id)
            client = self._load(run_id, row[0])
            result = getattr(client, method)(*args)
            if write:
                db.execute("UPDATE local_run SET body=? WHERE id=?", (self._dump(client._runs[run_id]), run_id))
            return result

    def get_state(self, run_id): return self._call(run_id, "get_state", run_id)
    def get_inputs(self, run_id): return self._call(run_id, "get_inputs", run_id)
    def get_result(self, run_id, result_id): return self._call(run_id, "get_result", run_id, result_id)
    def get_coordinated_run(self, run_id): return self._call(run_id, "get_coordinated_run", run_id)
    def history(self, run_id): return self._call(run_id, "history", run_id)
    def acquire_execution(self, req): return self._call(req.run_id, "acquire_execution", req, write=True)
    def commit_result(self, req): return self._call(req.run_id, "commit_result", req, write=True)
    def finish_unchanged(self, req): return self._call(req.run_id, "finish_unchanged", req, write=True)
    def save_coordination(self, req): return self._call(req.run_id, "save_coordination", req, write=True)
    def answer_question(self, req): return self._call(req.run_id, "answer_question", req, write=True)
    def request_final_review(self, req): return self._call(req.run_id, "request_final_review", req, write=True)
    def final_review(self, req): return self._call(req.run_id, "final_review", req, write=True)
    def reserve_llm_attempt(self, req): return self._call(req.run_id, "reserve_llm_attempt", req, write=True)
    def resume_run(self, req): return self._call(req.run_id, "resume_run", req, write=True)
