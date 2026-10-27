"""③ 호출 전 검사 (공통기준 8장, 오케스트레이션 책임).

반환: Gate(code, reason) 또는 None(호출 가능).
  INVALID_INPUT · VERSION_CONFLICT · UPSTREAM_NOT_READY → 에이전트를 호출하지 않는다 (오류 코드)
  WAITING_REVIEW → PM 검토 전. 오류가 아니라 대기
"""
from dataclasses import dataclass

from schema.schemas import Agent3Input

from app.kb.loader import baselines, documents


@dataclass(frozen=True)
class Gate:
    code: str
    reason: str


def preflight_a3(raw: dict | Agent3Input) -> Gate | None:
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
