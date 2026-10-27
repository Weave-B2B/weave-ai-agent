"""오케스트레이션 엔진: 판단하지 않는 결정적 워크플로 (LLM 없음). 공통기준 0·8·11장.

요청 → ① → [PM 항목 확인] → ② → [PM 검토] → ③ → [리드 검토] → 완료
순서·멈춤·재개·재실행·무효화·상태 저장만 한다. 판단·질문 대신 쓰기는 하지 않는다.
LangGraph 부모 그래프로 옮길 때도 이 클래스의 전이 규칙을 노드로 옮기면 된다.
"""
import concurrent.futures as cf
import contextvars
import time
from typing import Callable

from pydantic import ValidationError
from schema.schemas import (
    Agent1Input, Agent2Input, Agent3Input, Agent3Output, AgentError, Answer, AnswerAnalysisInput, Item,
    Meta, PMReview, RERUN_TABLE, Question, WorkflowState,
)

from app.agents.agent2.guards import apply_guards
from app.agents.agent3.guards import finalize_estimates
from app.agents.base import AgentFailure, AgentSet
from app.core.config import settings
from app.kb.loader import baselines, rate_cards
from app.orchestration.ids import new_result_id, now_iso
from app.orchestration.preflight import preflight_a3
from app.orchestration.state_store import CaseRecord, StateStore
from app.tools.errors import ToolInputError
from app.tools.trace import current_result_id

_RERUN_KIND = {"agent1": "request_changed", "agent2": "baseline_or_evidence", "agent3": "estimate_input_changed"}
_STAGE_OF = {"a1": "agent1", "a2": "agent2", "a3": "agent3"}


class VersionConflict(Exception):
    """오래된 화면의 덮어쓰기 차단 (state_revision·target_result_id 불일치)."""


class InvalidTransition(Exception):
    """현재 단계에서 받을 수 없는 입력."""


class InvalidInput(Exception):
    pass


class Engine:
    def __init__(self, agents: AgentSet, store: StateStore):
        self.agents, self.store = agents, store

    # ------------------------------------------------------------------ 외부 진입점
    def start(self, inp: Agent1Input, rate_card_id: str) -> CaseRecord:
        ctx = inp.context
        bl = baselines().get(ctx.baseline_id or "")
        if bl is None or bl["project_id"] != ctx.project_id or bl["as_of"] != ctx.baseline_as_of:
            raise InvalidInput("baseline_id·baseline_as_of가 프로젝트와 맞지 않음")
        if rate_card_id not in rate_cards():
            raise InvalidInput(f"unknown rate_card_id {rate_card_id}")
        old = self.store.get(ctx.case_id)
        rec = CaseRecord(inp=inp, rate_card_id=rate_card_id, state=WorkflowState(context=ctx))
        if old is not None:
            # 원문이 바뀐 재요청: revision이 올라야 하고, 이전 결과는 모두 STALE로 보존 (RERUN_TABLE request_changed)
            if ctx.request_revision <= old.inp.context.request_revision or ctx.analysis_id == old.inp.context.analysis_id:
                raise VersionConflict("원문 변경은 request_revision 증가와 새 analysis_id가 필요")
            self._stale(old, RERUN_TABLE["request_changed"][1])
            rec.state.stale_result_ids = list(old.state.stale_result_ids)
            rec.archive = dict(old.archive)
        self._event(rec, "start", "system")
        self._advance(rec)
        return rec

    def confirm_items(self, case_id: str, items: list[Item], state_revision: int) -> CaseRecord:
        rec = self._load(case_id, state_revision, expect_stage="pm_items")
        a1_by_id = {i.item_id: i for i in rec.a1.items}
        out, changed = [], []
        for it in items:
            if it.case_id != case_id or not it.item_id.startswith(case_id + "-"):
                raise InvalidInput(f"다른 case 항목 {it.item_id}")
            base = a1_by_id.get(it.item_id)
            if base is None or base.model_dump(exclude={"source"}) != it.model_dump(exclude={"source"}):
                it = it.model_copy(update={"source": "pm"})  # PM이 고친 항목은 source=pm
                changed.append(it.item_id)
            out.append(it)
        rec.items = out
        self._event(rec, "items_confirmed", "pm", changed=changed)
        self._advance(rec)
        return rec

    def submit_pm_review(self, case_id: str, review: PMReview, state_revision: int, new_evidence: bool = False) -> CaseRecord:
        rec = self._load(case_id, state_revision)
        if rec.a2 is None:
            raise InvalidTransition("② 결과가 아직 없음")
        if review.target_result_id != rec.a2.meta.result_id:
            raise VersionConflict("pm_review.target_result_id ≠ 현재 a2 result_id")
        if rec.pm_review is not None:
            self._stale(rec, ["pm_review", "a3"])      # PM 검토 변경(새 근거 아님) → ③부터
        rec.pm_review = review
        self._event(rec, "pm_review_submitted", review.reviewer, review_status=review.status)
        if new_evidence:                                # 새 근거가 생긴 변경 → ②부터
            self._invalidate_from(rec, "agent2")
        self._advance(rec)
        return rec

    def lead_decision(self, case_id: str, approved: bool, state_revision: int, reason: str = "") -> CaseRecord:
        rec = self._load(case_id, state_revision, expect_stage="lead_review")
        self._event(rec, "lead_approved" if approved else "lead_rejected", "lead", reason=reason)
        if approved:
            rec.state.status, rec.state.stage = "COMPLETED", None
        else:
            self._invalidate_from(rec, "agent3")
            self._advance(rec)
        return rec

    def answer_question(self, case_id: str, question_id: str, answer: Answer, state_revision: int) -> CaseRecord:
        rec = self._load(case_id, state_revision)
        q = rec.questions.get(question_id)
        if q is None or answer.question_id != question_id:
            raise InvalidInput("없는 질문")
        rec.answers.append(answer)
        rec.questions[question_id] = q.model_copy(update={"status": "answered"})  # 업로드만으로 해소하지 않는다
        self._event(rec, "answer_added", answer.answered_by, question_id=question_id)
        try:
            res = self.agents.agent1.analyze_answer(AnswerAnalysisInput(context=rec.inp.context, question=q, answer=answer))
        except Exception as e:  # 답변 분석 실패가 흐름을 죽이지는 않는다. 질문은 answered로 남는다.
            self._event(rec, "answer_analysis_failed", "system", message=type(e).__name__)
            self._sync_questions(rec)
            return rec
        if res.resolved:
            rec.questions[question_id] = rec.questions[question_id].model_copy(update={"status": "resolved"})
        if res.rerun_from:
            self._invalidate_from(rec, res.rerun_from)
        self._advance(rec)
        return rec

    def retry(self, case_id: str, state_revision: int) -> CaseRecord:
        """기술 실패(FAILED)한 단계를 새 result_id로 다시 실행."""
        rec = self._load(case_id, state_revision)
        if rec.state.status != "FAILED":
            raise InvalidTransition("FAILED 상태가 아님")
        rec.state.error = None
        self._advance(rec)
        return rec

    # ------------------------------------------------------------------ 전이
    def _advance(self, rec: CaseRecord) -> None:
        s = rec.state
        s.status, s.rerun_from = "RUNNING", None
        ctx = rec.inp.context

        if rec.a1 is None:
            s.stage = "agent1"
            out = self._run("agent1", rec, lambda: self.agents.agent1.run(rec.inp.model_copy(update={"answers": rec.answers})), [])
            if out is None:
                return
            rec.a1, s.a1_result_id = out, out.meta.result_id
            self._sync_questions(rec)
            if out.status == "NEEDS_INPUT":
                return self._wait(rec, "WAITING_INPUT", "agent1")
            return self._wait(rec, "WAITING_PM_REVIEW", "pm_items")   # PM 항목 확인에서 멈춤

        if rec.items is None:
            return self._wait(rec, "WAITING_PM_REVIEW", "pm_items")

        functions = [i for i in rec.items if i.item_type == "function"]
        if not functions:                               # 요청이 아닌 메일: 판단·산정할 것이 없다
            s.status, s.stage = "COMPLETED", None
            return self._event(rec, "no_function_items", "system")

        if rec.a2 is None:
            s.stage = "agent2"
            a2_in = Agent2Input(context=ctx, message_text=rec.inp.message_text, a1_result_id=s.a1_result_id, items=functions,
                                constraints=self._constraints(rec), answers=rec.answers)
            out = self._run("agent2", rec, lambda: self.agents.agent2.run(a2_in), [s.a1_result_id])
            if out is None:
                return
            out = apply_guards(out)                     # 인용 검증·근거 없음 → unclear (코드)
            rec.a2, s.a2_result_id = out, out.meta.result_id
            self._sync_questions(rec)
            if out.status == "NEEDS_INPUT":
                return self._wait(rec, "WAITING_INPUT", "agent2")
            return self._wait(rec, "WAITING_PM_REVIEW", "pm_review")

        if rec.pm_review is None or rec.pm_review.status != "REVIEWED":
            return self._wait(rec, "WAITING_PM_REVIEW", "pm_review")
        s.pm_review_id = rec.pm_review.review_id

        a3_in = Agent3Input(context=ctx, rate_card_id=rec.rate_card_id, received_at=rec.inp.received_at, items=functions,
                            a2_result=rec.a2, pm_review=rec.pm_review, constraints=self._constraints(rec), answers=rec.answers)
        gate = preflight_a3(a3_in)
        if gate is not None:
            if gate.code == "WAITING_REVIEW":
                return self._wait(rec, "WAITING_PM_REVIEW", "pm_review")
            return self._fail(rec, AgentError(code=gate.code, message=gate.reason, stage="agent3", retryable=False))

        if rec.a3 is None:
            s.stage = "agent3"
            ups = [s.a2_result_id, s.pm_review_id]
            if not a3_in.targets():                     # 산정 대상 없음 → ③ 호출 없이 NOT_REQUIRED
                out = Agent3Output(context=ctx, meta=self._stamp(Meta(), "agent3", rec, ups, 0), status="NOT_REQUIRED", estimates=[])
            else:
                out = self._run("agent3", rec, lambda: finalize_estimates(self.agents.agent3.run(a3_in), rec.rate_card_id), ups)
                if out is None:
                    return
            rec.a3, s.a3_result_id = out, out.meta.result_id
            self._sync_questions(rec)

        if rec.a3.status == "NEEDS_INPUT":
            return self._wait(rec, "WAITING_INPUT", "agent3")
        if rec.a3.status == "NOT_REQUIRED":
            s.status, s.stage = "COMPLETED", None       # 처리 종료일 뿐 확인 질문이 해소됐다는 뜻은 아니다
            return self._event(rec, "not_required", "system")
        return self._wait(rec, "WAITING_PM_REVIEW", "lead_review")

    def _run(self, stage: str, rec: CaseRecord, call: Callable, upstream: list[str]):
        """에이전트 1회 실행. 출력 형식 오류는 1회 재요청, retryable 실패는 최대 2회 재시도, 아니면 FAILED."""
        rid = new_result_id(stage, rec.inp.context.case_id)
        attempts_left_invalid, attempts_left_retry, t0 = 1, settings.tool_max_retries, time.monotonic()
        while True:
            try:
                ctxvars = contextvars.copy_context()
                ctxvars.run(current_result_id.set, rid)      # 도구 호출 로그에 result_id를 남기기 위해
                ex = cf.ThreadPoolExecutor(1)
                try:
                    out = ex.submit(ctxvars.run, call).result(timeout=settings.stage_timeout_sec)
                finally:
                    ex.shutdown(wait=False)                  # 시간 초과 시 스레드 종료를 기다리며 막히지 않도록
                if out.context != rec.inp.context:
                    raise ValueError("output context mismatch")
                if out.status == "ERROR":
                    return self._fail(rec, out.error)
                out.meta = self._stamp(out.meta, stage, rec, upstream, int((time.monotonic() - t0) * 1000), rid)
                return out
            except cf.TimeoutError:
                return self._fail(rec, AgentError(code="MODEL_TIMEOUT", message=f"{stage} 시간 초과", stage=stage, retryable=True))
            except AgentFailure as e:
                if e.error.retryable and attempts_left_retry > 0:
                    attempts_left_retry -= 1
                    continue
                return self._fail(rec, e.error)
            except ToolInputError as e:
                return self._fail(rec, AgentError(code="INVALID_INPUT", message=str(e), stage=stage))
            except (ValidationError, ValueError):
                if attempts_left_invalid > 0:
                    attempts_left_invalid -= 1
                    continue
                return self._fail(rec, AgentError(code="OUTPUT_INVALID", message=f"{stage} 출력 검증 실패", stage=stage, retryable=True))

    # ------------------------------------------------------------------ 보조
    def _constraints(self, rec: CaseRecord) -> list[Item]:
        # question(견적 요청) 항목은 A2·A3 어느 쪽에도 넘기지 않는다 (v0.5 CHANGELOG)
        return [i for i in rec.items if i.item_type in ("constraint", "deadline", "dependency")]

    def _stamp(self, meta: Meta, stage: str, rec: CaseRecord, upstream: list[str], latency_ms: int, rid: str | None = None) -> Meta:
        n = 1 + sum(1 for k in rec.archive if k.startswith(f"{stage}-"))
        return meta.model_copy(update={"result_id": rid or new_result_id(stage, rec.inp.context.case_id), "result_revision": n,
                                       "created_at": now_iso(), "upstream_result_ids": upstream,
                                       "latency_ms": meta.latency_ms if meta.latency_ms is not None else latency_ms})

    def _invalidate_from(self, rec: CaseRecord, stage: str) -> None:
        kind = _RERUN_KIND[stage]
        targets = RERUN_TABLE[kind][1]
        self._stale(rec, targets)
        rec.state.rerun_from = stage
        self._event(rec, "rerun", "system", from_stage=stage)

    def _stale(self, rec: CaseRecord, targets: list[str]) -> None:
        """무효화된 결과는 삭제하지 않고 stale_result_ids·archive에 보존한다."""
        for t in targets:
            obj = {"a1": rec.a1, "a2": rec.a2, "a3": rec.a3, "pm_review": rec.pm_review}[t]
            if obj is None:
                continue
            rid = obj.review_id if t == "pm_review" else obj.meta.result_id
            rec.state.stale_result_ids.append(rid)
            rec.archive[rid] = obj.model_dump(mode="json")
            setattr(rec, t, None)
            setattr(rec.state, {"a1": "a1_result_id", "a2": "a2_result_id", "a3": "a3_result_id", "pm_review": "pm_review_id"}[t], None)
        if "a1" in targets:
            rec.items = None

    def _sync_questions(self, rec: CaseRecord) -> None:
        for out in (rec.a1, rec.a2, rec.a3):
            for q in (out.questions if out else []):
                rec.questions.setdefault(q.question_id, q)
        rec.state.open_question_ids = [q.question_id for q in rec.questions.values() if q.status == "open"]

    def _wait(self, rec: CaseRecord, status: str, stage: str) -> None:
        rec.state.status, rec.state.stage = status, stage
        self._event(rec, f"wait:{stage}", "system")

    def _fail(self, rec: CaseRecord, err: AgentError | None):
        """기술 실패. ERROR 단계의 부분 출력은 다음 단계로 넘기지 않는다."""
        rec.state.status, rec.state.error = "FAILED", err
        self._event(rec, "failed", "system", code=err.code if err else None)
        return None

    def _event(self, rec: CaseRecord, event: str, by: str, **extra) -> None:
        rec.state.state_revision += 1
        rec.state.history.append({"at": now_iso(), "event": event, "by": by, "state_revision": rec.state.state_revision, **extra})
        self._sync_questions(rec)
        self.store.put(rec)

    def _load(self, case_id: str, state_revision: int, expect_stage: str | None = None) -> CaseRecord:
        rec = self.store.get(case_id)
        if rec is None:
            raise InvalidInput("없는 case")
        if state_revision != rec.state.state_revision:
            raise VersionConflict(f"state_revision {state_revision} ≠ 현재 {rec.state.state_revision}")
        if expect_stage and rec.state.stage != expect_stage:
            raise InvalidTransition(f"현재 단계 {rec.state.stage}에서는 받을 수 없음 (필요: {expect_stage})")
        return rec
