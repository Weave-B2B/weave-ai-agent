"""Small in-memory Agent3 workflow, independent of the shared A1/A2 engine.

This is an AI-server integration API for a trusted backend, not an authenticated
end-user approval endpoint. Replace the in-memory store when CN persistence is ready.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agents.agent3.guards import finalize_estimates
from app.agents.agent3.prompts import TEMPLATE_VERSION
from app.agents.base import Agent3
from app.kb.loader import rate_cards
from app.orchestration.ids import new_result_id, now_iso
from app.tools.trace import current_result_id
from schema.schemas import Agent3Input, Agent3Output, Draft, Line


class WorkflowConflict(ValueError):
    pass


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EstimateEdit(_Request):
    item_ids: list[str] = Field(min_length=1)
    lines: list[Line] = Field(min_length=1)


class EditImpact(_Request):
    state_revision: int = Field(ge=1)
    target_result_id: str
    estimates: list[EstimateEdit] = Field(min_length=1)
    reason: str = Field(min_length=1)


class ReviewImpact(_Request):
    state_revision: int = Field(ge=1)
    target_result_id: str
    snapshot_hash: str
    request_id: str = Field(min_length=1)
    reviewer_ref: str = Field(min_length=1)
    approved: bool
    reason: str = ""
    acknowledge_unknown_schedule: bool = False


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _rate_hash(rate_card_id: str) -> str:
    cards = rate_cards()
    if rate_card_id not in cards:
        raise ValueError("unknown rate card")
    return _hash(cards[rate_card_id])


def _snapshot_hash(out: Agent3Output) -> str:
    return _hash({"context": out.context.model_dump(mode="json"),
        "upstream_result_ids": out.meta.upstream_result_ids,
        "estimates": [e.model_dump(mode="json") for e in out.estimates],
        "questions": [q.model_dump(mode="json") for q in out.questions]})


def _build_draft(inp: Agent3Input, out: Agent3Output, review_id: str) -> Draft:
    selected = {i for e in out.estimates for i in e.item_ids}
    assessments = {a.item_id: a for a in inp.a2_result.assessments}
    reviews = {r.item_id: r for r in inp.pm_review.items}
    scope, impact, costs = [], [], []
    for item in inp.items:
        if item.item_id not in selected:
            continue
        scope.append(f"{item.item_id} {item.text}: {reviews[item.item_id].pm_label} (PM 검토)")
        if reviews[item.item_id].reason:
            scope.append(f"PM 검토 사유: {reviews[item.item_id].reason}")
        for evidence in assessments[item.item_id].evidence:
            if evidence.verified is True:
                scope.append(f"{evidence.doc_id}:{evidence.section_id} {evidence.quote}")
    for estimate in out.estimates:
        for line in estimate.lines:
            impact.append(f"{line.role} {line.md:g}MD · {line.task} (가정: {line.assumption or '없음'})")
        schedule = estimate.schedule
        impact.append(f"일정 영향: {schedule.business_days} 영업일" if schedule.business_days is not None
            else f"일정 영향 미산정: {schedule.reason}")
        if estimate.totals:
            costs.append(f"판매 산정가 {estimate.totals.price:,}원 / VAT 별도 / 단가표 {estimate.rate_card_id}")
    impact.extend(f"조건: {i.text}" for i in inp.constraints)
    return Draft(summary=f"[{inp.context.case_id}] 변경 요청 협의안 — 내부 검토용 초안",
        scope="\n".join(scope), impact="\n".join(impact), cost="\n".join(costs) or None,
        open_questions=[q.text for q in out.questions if q.status in ("open", "answered")],
        source_result_ids=[inp.a2_result.meta.result_id, inp.pm_review.review_id, out.meta.result_id, review_id])


@dataclass
class _Session:
    inp: Agent3Input
    view: dict
    rate_hash: str
    history: list[dict]


class Agent3Workflow:
    def __init__(self, agent: Agent3):
        self.agent = agent
        self._sessions: dict[str, _Session] = {}
        self._current: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def prepare(self, inp: Agent3Input) -> dict:
        # Copy upstream data; never mutate another agent's result or PMReview.
        inp = inp.model_copy(deep=True)
        before = _rate_hash(inp.rate_card_id)
        rid = new_result_id("agent3-impact", inp.context.case_id)
        token = current_result_id.set(rid)
        try:
            out = self.agent.run(inp)
        finally:
            current_result_id.reset(token)
        if before != _rate_hash(inp.rate_card_id):
            raise WorkflowConflict("rate card changed during estimation; calculate again")
        if out.context != inp.context or out.draft is not None:
            raise ValueError("impact phase must return matching context and no draft")
        self._validate_targets(inp, out)
        out.meta.result_id, out.meta.created_at = rid, now_iso()
        out.meta.upstream_result_ids = [inp.a2_result.meta.result_id, inp.pm_review.review_id]
        sid = f"a3-{uuid4().hex}"
        phase = self._phase(out)
        view = {"session_id": sid, "state_revision": 1, "phase": phase,
            "rate_card_fingerprint": before,
            "snapshot_hash": _snapshot_hash(out), "impact": out.model_dump(mode="json"),
            "approval": None, "draft": None, "draft_result_id": None,
            "template_version": TEMPLATE_VERSION, "storage": "memory"}
        with self._lock:
            self._sessions[sid] = _Session(inp, view, before, [])
            self._current[(inp.context.project_id, inp.context.case_id)] = sid
        return self.get(sid)

    def get(self, session_id: str) -> dict:
        with self._lock:
            session = self._load(session_id)
            return json.loads(json.dumps({**session.view, "history": session.history}, ensure_ascii=False))

    def edit(self, session_id: str, req: EditImpact) -> dict:
        with self._lock:
            s = self._load(session_id)
            self._check(s, req.state_revision, req.target_result_id)
            out = Agent3Output.model_validate(s.view["impact"])
            edits = {tuple(sorted(e.item_ids)): e for e in req.estimates}
            if len(edits) != len(req.estimates) or set(edits) != {tuple(sorted(e.item_ids)) for e in out.estimates}:
                raise ValueError("edit must keep the current estimate item groups")
            for estimate in out.estimates:
                edit = edits[tuple(sorted(estimate.item_ids))]
                if any(not math.isfinite(line.md) or line.md < 0 or (line.md * 2) % 1 for line in edit.lines):
                    raise ValueError("MD must be finite, nonnegative and use 0.5 steps")
                estimate.lines, estimate.status = edit.lines, "draft"
            out.questions = [q for q in out.questions if q.question_id != f"{s.inp.context.case_id}-A3Q9"]
            out.status = "NEEDS_INPUT" if any(q.blocking and q.status in ("open", "answered") for q in out.questions) else "IMPACT_DRAFT_READY"
            self._validate_targets(s.inp, out)
            rid = new_result_id("agent3-impact", s.inp.context.case_id)
            before = _rate_hash(s.inp.rate_card_id)
            token = current_result_id.set(rid)
            try:
                out = finalize_estimates(out, s.inp.rate_card_id)
            finally:
                current_result_id.reset(token)
            if before != _rate_hash(s.inp.rate_card_id):
                raise WorkflowConflict("rate card changed during calculation")
            s.history.append({"impact": s.view["impact"], "approval": s.view["approval"],
                "draft": s.view["draft"], "draft_result_id": s.view["draft_result_id"], "reason": req.reason})
            out.meta.result_id, out.meta.created_at = rid, now_iso()
            out.meta.result_revision += 1
            s.rate_hash = before
            s.view.update(impact=out.model_dump(mode="json"), snapshot_hash=_snapshot_hash(out),
                rate_card_fingerprint=before,
                approval=None, draft=None, draft_result_id=None, phase=self._phase(out))
            s.view["state_revision"] += 1
            return self.get(session_id)

    def review(self, session_id: str, req: ReviewImpact) -> dict:
        with self._lock:
            s = self._load(session_id)
            old = s.view["approval"]
            request_hash = _hash(req.model_dump(mode="json"))
            if old and old["request_id"] == req.request_id:
                if old["request_hash"] != request_hash:
                    raise WorkflowConflict("request_id reused with a different decision")
                self._check_current(s)
                self._check_rates(s)
                return self.get(session_id)
            self._check(s, req.state_revision, req.target_result_id)
            self._check_rates(s)
            if req.snapshot_hash != s.view["snapshot_hash"]:
                raise WorkflowConflict("snapshot changed; reload before review")
            out = Agent3Output.model_validate(s.view["impact"])
            if s.view["phase"] != "WAITING_IMPACT_APPROVAL":
                raise WorkflowConflict("impact is not awaiting approval")
            if req.approved:
                if any(e.status != "draft" or e.totals is None for e in out.estimates):
                    raise ValueError("all selected estimates need task and cost results before approval")
                if any(q.blocking and q.status in ("open", "answered") for q in out.questions):
                    raise ValueError("blocking questions must be resolved before approval")
                if any(e.schedule.business_days is None for e in out.estimates) and not req.acknowledge_unknown_schedule:
                    raise ValueError("acknowledge the unestimated schedule before approval")
            elif not req.reason.strip():
                raise ValueError("rejection needs a reason")
            approval = {"review_id": f"a3-review-{uuid4().hex}", "target_result_id": req.target_result_id,
                "snapshot_hash": req.snapshot_hash, "approved": req.approved,
                "reviewer_ref": req.reviewer_ref, "reviewed_at": now_iso(), "reason": req.reason,
                "request_id": req.request_id, "request_hash": request_hash,
                "acknowledge_unknown_schedule": req.acknowledge_unknown_schedule}
            # Compose before committing state so a failed template never leaves a half-applied review.
            draft = _build_draft(s.inp, out, approval["review_id"]) if req.approved else None
            self._check_rates(s)
            if old:
                s.history.append({"approval": old, "reason": "review_replaced"})
            s.view.update(approval=approval, draft=draft.model_dump(mode="json") if draft else None,
                draft_result_id=new_result_id("agent3-draft", s.inp.context.case_id) if draft else None,
                phase="DRAFT_READY" if draft else "WAITING_IMPACT_APPROVAL")
            s.view["state_revision"] += 1
            return self.get(session_id)

    def _load(self, sid: str) -> _Session:
        if sid not in self._sessions:
            raise KeyError("unknown Agent3 session")
        return self._sessions[sid]

    def _check(self, s: _Session, revision: int, target: str):
        self._check_current(s)
        if revision != s.view["state_revision"] or target != s.view["impact"]["meta"]["result_id"]:
            raise WorkflowConflict("stale result or state revision")

    def _check_current(self, s: _Session):
        if self._current[(s.inp.context.project_id, s.inp.context.case_id)] != s.view["session_id"]:
            raise WorkflowConflict("a newer impact exists for this case")

    @staticmethod
    def _check_rates(s: _Session):
        if _rate_hash(s.inp.rate_card_id) != s.rate_hash:
            raise WorkflowConflict("rate card changed; recalculate and review the new result")

    @staticmethod
    def _phase(out: Agent3Output) -> str:
        return {"NOT_REQUIRED": "NOT_REQUIRED", "NEEDS_INPUT": "WAITING_INPUT",
            "WAITING_REVIEW": "WAITING_UPSTREAM_REVIEW", "ERROR": "FAILED"}.get(out.status, "WAITING_IMPACT_APPROVAL")

    @staticmethod
    def _validate_targets(inp: Agent3Input, out: Agent3Output):
        if out.status in ("NOT_REQUIRED", "WAITING_REVIEW", "ERROR"):
            return
        selected = {i.item_id for i in inp.targets()}
        actual = [item for e in out.estimates for item in e.item_ids]
        if len(actual) != len(set(actual)) or set(actual) != selected:
            raise ValueError("impact target mismatch")
        for estimate in out.estimates:
            if any(not set(line.item_ids) <= set(estimate.item_ids) for line in estimate.lines):
                raise ValueError("task refers to an unselected item")
            if estimate.status == "draft" and {i for line in estimate.lines for i in line.item_ids} != set(estimate.item_ids):
                raise ValueError("task lines do not cover every estimate item")
