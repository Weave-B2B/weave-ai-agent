"""Local Supervisor sessions, with optional SQLite checkpoints."""
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agents.agent1.agent import LlmAgent1
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import WorkflowConflict
from app.agents.base import AgentFailure
from app.agents.registry import build_agent2
from app.core.config import settings
from app.llm.client import build_llm
from app.orchestration.progress import CaseProgress
from app.orchestration.supervisor_start import start_supervisor, wire_supervisor
from app.orchestration.supervisor_store import checkpoint, local_store, restore_host
from schema.schemas import Agent1Input

router = APIRouter(prefix="/prototype/supervisor", tags=["Prototype supervisor"])
_sessions = {}
_lock = RLock()
_step_locks = {}
_progress = {}


@dataclass
class LocalSession:
    runtime: object
    provider: str
    storage_revision: int | None = None


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Agent1Input
    rate_card_id: str
    provider: Literal["gpt", "local"] = "gpt"


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    text: str = Field(min_length=1, max_length=4000)
    state_revision: int = Field(ge=0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state_revision: int = Field(ge=0)
    expected_snapshot_hash: str = Field(min_length=1)
    decision: Literal["approve", "hold", "reject"]
    reason: str = ""
    acknowledge_unknown_schedule: bool = False


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state_revision: int = Field(ge=0)


def _workers(provider):
    if provider == "gpt":
        model_settings = {"llm_provider": "openai", "llm_model": "gpt-6-luna"}
    else:
        model_settings = {"llm_provider": "ollama", "ollama_model": "gpt-oss:20b",
                          "ollama_base_url": settings.ollama_base_url or "http://localhost:11434"}
    llm = build_llm(settings.model_copy(update=model_settings))
    return dict(agent1=LlmAgent1(llm), agent2=build_agent2(llm), agent3=LlmAgent3(llm), llm=llm, draft_llm=llm)


def _saved(case_id):
    try:
        store = local_store(settings.prototype_supervisor_db)
        return store.load(case_id) if store else None
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(503, "로컬 Supervisor 저장 상태를 읽을 수 없습니다.") from exc


def _session(case_id):
    with _lock:
        entry = _sessions.get(case_id)
        if entry is not None:
            return entry
        row = _saved(case_id)
        if row is None:
            raise HTTPException(404, "저장된 실행을 찾을 수 없습니다.")
        revision, payload = row
        try:
            state = restore_host(case_id, payload)
            provider = payload["provider"]
            runtime = wire_supervisor(state, **_workers(provider))
            runtime.history = payload["history"]
            if payload["a3_session"] is not None:
                runtime.agent3.restore_checkpoint(case_id, payload["a3_session"])
            progress = CaseProgress(payload["progress"])
        except (KeyError, ValueError, WorkflowConflict) as exc:
            raise HTTPException(503, "저장된 Supervisor 실행을 복구할 수 없습니다.") from exc
        entry = LocalSession(runtime, provider, revision)
        _sessions[case_id], _progress[case_id] = entry, progress
        return entry


def _save(case_id, entry, progress):
    try:
        store = local_store(settings.prototype_supervisor_db)
        if store:
            entry.storage_revision = store.save(case_id, checkpoint(entry.runtime, entry.provider, progress),
                                                entry.storage_revision)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, "로컬 DB에 Supervisor 상태를 저장할 수 없습니다.") from exc


def _case_lock(case_id):
    with _lock:
        return _step_locks.setdefault(case_id, RLock())


@contextmanager
def _change(case_id):
    with _case_lock(case_id):
        entry = _session(case_id)
        progress = _progress[case_id]
        try:
            with progress.capture():
                yield entry.runtime, progress
            _save(case_id, entry, progress)
        except Exception:
            if settings.prototype_supervisor_db is not None:
                # The durable checkpoint remains authoritative after a rejected change.
                with _lock:
                    _sessions.pop(case_id, None)
                    _progress.pop(case_id, None)
            raise


def _view(session, progress):
    state = session.state
    with state.lock:
        view = state.a3_view if not state.a3_stale else None
        if view and settings.prototype_supervisor_db is not None:
            view = {**view, "storage": "sqlite"}
        review = state.final_review
        ready = bool(view and view.get("can_final_review") and not state.pending_question
                     and not state.answers_pending_review and not any(n.get("blocking") and not n.get("answered") for n in state.last_needs))
        return {"case_id": state.a2.context.case_id, "state_revision": state.a2.state_revision,
                "a1": state.a1_result.model_dump(mode="json"),
                "a2": state.a2.a2_result.model_dump(mode="json") if state.a2.a2_result else None,
                "agent3": view, "agent3_stale": state.a3_stale,
                "question": dict(state.pending_question) if state.pending_question else None,
                "needs": list(state.last_needs), "archived_needs": list(state.archived_needs),
                "hold_reason": state.hold_reason,
                "ready_for_review": ready, "review": review,
                "history": list(session.history), "progress": progress.after()}


@router.post("/cases")
def start(req: StartRequest):
    case_id = req.input.context.case_id
    progress = CaseProgress()
    with _lock:
        if case_id in _sessions or case_id in _progress or _saved(case_id) is not None:
            raise HTTPException(409, "이미 실행 중인 요청입니다.")
        _progress[case_id] = progress
    try:
        with progress.capture():
            progress.start("Agent 1", "고객 요청을 읽고 검토 항목으로 정리하고 있습니다")
            session = start_supervisor(req.input, req.rate_card_id, **_workers(req.provider))
        entry = LocalSession(session, req.provider)
        _save(case_id, entry, progress)
        with _lock:
            _sessions[case_id] = entry
    except AgentFailure as exc:
        raise HTTPException(502, exc.error.message) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    finally:
        with _lock:
            if case_id not in _sessions:
                _progress.pop(case_id, None)
    return _view(session, progress)


@router.get("/cases/{case_id}")
def get(case_id: str):
    with _case_lock(case_id):
        return _view(_session(case_id).runtime, _progress[case_id])


@router.get("/cases/{case_id}/progress")
def get_progress(case_id: str, after: int = 0):
    with _lock:
        progress = _progress.get(case_id)
    # A poll can arrive just before the start request reserves the case.
    return {"events": progress.after(after) if progress else []}


@router.post("/cases/{case_id}/step")
def step(case_id: str):
    try:
        with _change(case_id) as (session, progress):
            event = session.step()
            view = _view(session, progress)
    except AgentFailure as exc:
        raise HTTPException(502, exc.error.message) from None
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"event": event, "state": view}


@router.post("/cases/{case_id}/answer")
def answer(case_id: str, req: AnswerRequest):
    try:
        with _change(case_id) as (session, progress):
            session.answer(req.question_id, req.text, req.state_revision)
            view = _view(session, progress)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return view


@router.post("/cases/{case_id}/review")
def review(case_id: str, req: ReviewRequest):
    try:
        with _change(case_id) as (session, progress):
            session.review(decision=req.decision, state_revision=req.state_revision,
                expected_snapshot_hash=req.expected_snapshot_hash, reason=req.reason,
                acknowledge_unknown_schedule=req.acknowledge_unknown_schedule)
            view = _view(session, progress)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return view


@router.post("/cases/{case_id}/resume")
def resume(case_id: str, req: ResumeRequest):
    try:
        with _change(case_id) as (session, progress):
            session.resume(req.state_revision)
            view = _view(session, progress)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return view
