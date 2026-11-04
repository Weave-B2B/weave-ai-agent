"""③ 호출 전 검사 (공통기준 8장, 오케스트레이션 책임).

반환: Gate(code, reason) 또는 None(호출 가능).
  INVALID_INPUT · VERSION_CONFLICT · UPSTREAM_NOT_READY → 에이전트를 호출하지 않는다 (오류 코드)
  WAITING_REVIEW → PM 검토 전. 오류가 아니라 대기
"""
from dataclasses import dataclass

from schema.schemas import Agent3Input
from app.agents.agent3.contracts import ProvisionalAgent3Input

from app.kb.loader import baselines, documents, superseded_by


@dataclass(frozen=True)
class Gate:
    code: str
    reason: str


def preflight_a3(raw: dict | Agent3Input) -> Gate | None:
    if isinstance(raw, ProvisionalAgent3Input):
        return preflight_provisional(raw)
    try:
        inp = raw if isinstance(raw, Agent3Input) else Agent3Input.model_validate(raw)
    except Exception as e:  # pydantic ValidationError 포함: 입력 스키마 불일치는 에이전트를 부르지 않는다
        return Gate("INVALID_INPUT", f"schema: {str(e).splitlines()[0]}")

    ctx, a2, rv = inp.context, inp.a2_result, inp.pm_review
    if a2.context != ctx:
        return Gate("INVALID_INPUT", "a2_result.context ≠ context")
    bl = baselines().get(ctx.baseline_id or "")
    if bl is None or bl["project_id"] != ctx.project_id or bl["as_of"] != ctx.baseline_as_of:
        return Gate("INVALID_INPUT", "baseline 불일치")
    for it in inp.items + inp.constraints:
        if it.case_id != ctx.case_id or not it.item_id.startswith(ctx.case_id + "-"):
            return Gate("INVALID_INPUT", f"다른 case 항목 {it.item_id}")
    for a in a2.assessments:
        for ev in a.evidence:  # 모든 문서 유형(변경합의서 포함)을 기준선 문서 목록과 프로젝트로 격리
            doc = documents().get(ev.doc_id)
            if ev.doc_id not in bl["document_ids"] or doc is None or doc["project_id"] != ctx.project_id:
                return Gate("INVALID_INPUT", f"기준선 밖 근거 {ev.doc_id}")
    if a2.status != "READY_FOR_REVIEW":
        return Gate("UPSTREAM_NOT_READY", f"a2 status {a2.status}")
    if rv.status != "REVIEWED":
        return Gate("WAITING_REVIEW", "PM 검토 전")
    if rv.target_result_id != a2.meta.result_id:
        return Gate("VERSION_CONFLICT", "pm_review.target_result_id ≠ 현재 a2 result_id")
    fn = {i.item_id for i in inp.items}
    if fn != {a.item_id for a in a2.assessments} or fn != {r.item_id for r in rv.items}:
        return Gate("INVALID_INPUT", "function 항목·② 평가·PM 검토 항목 집합 불일치")
    labels = {a.item_id: a.label for a in a2.assessments}
    if any(r.ai_label != labels[r.item_id] for r in rv.items):
        return Gate("INVALID_INPUT", "ai_label ≠ ② label")
    if any(q.blocking and q.status == "open" for q in a2.questions):
        return Gate("UPSTREAM_NOT_READY", "열린 blocking 질문")
    return None


def preflight_provisional(inp: ProvisionalAgent3Input) -> Gate | None:
    """Validate context before Agent2 quote guards; eligibility is checked per item by the tool."""
    ctx, a2 = inp.context, inp.a2_result
    bl = baselines().get(ctx.baseline_id or "")
    if a2.context != ctx or not a2.meta.result_id:
        return Gate("INVALID_INPUT", "Agent 2 context or result ID mismatch")
    if not bl or bl["project_id"] != ctx.project_id or bl["as_of"] != ctx.baseline_as_of:
        return Gate("INVALID_INPUT", "baseline mismatch")
    if bl.get("status") == "SUPERSEDED" or superseded_by(ctx.baseline_id):
        return Gate("VERSION_CONFLICT", "superseded baseline")
    ids = {i.item_id for i in inp.items}
    assessed = [a.item_id for a in a2.assessments]
    if len(assessed) != len(set(assessed)) or not set(assessed) <= ids:
        return Gate("INVALID_INPUT", "unknown or duplicate assessment")
    for item in inp.items + inp.constraints:
        if item.case_id != ctx.case_id or not item.item_id.startswith(ctx.case_id + "-"):
            return Gate("INVALID_INPUT", "foreign case item")
    for question in a2.questions:
        if question.case_id != ctx.case_id or not set(question.item_ids) <= ids:
            return Gate("INVALID_INPUT", "foreign question or item reference")
    if a2.status not in ("READY_FOR_REVIEW", "NEEDS_INPUT"):
        return Gate("UPSTREAM_NOT_READY", "Agent 2 is unavailable")
    allowed, _ = provisional_selection(inp)
    if not set(inp.target_item_ids) <= set(allowed):
        return Gate("UPSTREAM_NOT_READY", "selected items lack verified scope evidence or have blocking questions")
    return None


def provisional_selection(inp: ProvisionalAgent3Input):
    """Every estimate, including explicit quotes, needs verified out-of-scope evidence.

    A linked estimate_request is answered through its target items and is never
    estimated separately, so the same work is not priced twice.
    """
    guarded = guarded_provisional_scope(inp)
    assessments = {a.item_id: a for a in guarded.assessments}
    quote_targets = inp.quote_targets()
    display = {i.item_id: i.display_no for i in inp.items}
    allowed, deferred = [], []
    requested = set(inp.target_item_ids)
    for item in inp.items:
        if item.item_id not in requested:
            continue
        if item.item_id in inp.quote_links:
            linked = ", ".join(display[i] for i in inp.quote_links[item.item_id])
            deferred.append({"kind": "not_required", "item_ids": [item.item_id],
                "topic": f"견적 요청 - 연결 항목({linked}) 산정 결과로 답변"})
            continue
        a = assessments.get(item.item_id)
        blocking_questions = [q for q in guarded.questions if q.blocking and q.status in ("open", "answered") and
            (not q.item_ids or item.item_id in q.item_ids)]
        blocked = bool(blocking_questions)
        if blocked:
            for q in blocking_questions:
                deferred.append({"kind": "client_input", "item_ids": q.item_ids or list(display),
                    "topic": q.text, "target": q.target, "source": "agent2", "question_id": q.question_id})
            continue
        quote_request = item.intent == "estimate_request"
        change = item.intent == "change_request" or (
            item.intent == "confirmation" and item.item_id in inp.conditional_item_ids) or item.item_id in quote_targets
        candidate = quote_request or change
        eligible = candidate and a and a.label == "out_of_scope" and bool(a.evidence)
        if not blocked and eligible:
            allowed.append(item.item_id)
        else:
            kind = "client_input" if blocked else "more_evidence" if candidate and (not a or a.label == "unclear") else "not_required"
            topic = ("미해결 질문 확인" if blocked else
                "기존 범위 내 항목 - 견적 산정 대상 아님" if quote_request and a and a.label == "in_scope" else
                "계약 조건·제외 범위 확인" if kind == "more_evidence" else
                "기존 범위 내 또는 기승인 변경 - 산정 대상 아님" if change and a else "산정 대상 아님")
            deferred.append({"kind": kind, "item_ids": [item.item_id], "topic": topic})
    unique = {repr(sorted(d.items())): d for d in deferred}
    return allowed, list(unique.values())


def guarded_provisional_scope(inp: ProvisionalAgent3Input):
    from app.agents.agent2.guards import apply_guards
    out = inp.a2_result.model_copy(deep=True)
    for assessment in out.assessments:
        assessment.evidence = [ev for ev in assessment.evidence
            if documents().get(ev.doc_id, {}).get("version") == ev.doc_version]
    return apply_guards(out)
