"""Customer email preview from the current shared workflow, not a PM approval."""
import json
import re

from app.agents.agent3.workflow import DraftNarrative, FACTS_MARKER, _rate_hash
from app.orchestration.engine import InvalidTransition, VersionConflict
from app.orchestration.ids import new_result_id


def _facts(rec):
    labels = {"in_scope": "기존 계약 범위 내", "out_of_scope": "기존 계약 범위 밖",
              "approved_change": "기존 승인된 변경", "unclear": "범위 확인 필요"}
    by_id = {i.item_id: i for i in rec.items or []}
    facts = ["요청 사항과 범위 검토"]
    for review in rec.pm_review.items:
        item = by_id.get(review.item_id)
        if item:
            facts.append(f"- {item.text}: {labels.get(review.pm_label, '범위 확인 필요')}")
    estimates = rec.a3.estimates
    if estimates:
        facts.append("\n예상 작업 및 제안")
        for estimate in estimates:
            for line in estimate.lines:
                facts.append(f"- {line.task}: {line.md:g} MD")
            if estimate.totals is None:
                facts.append("- 해당 항목의 공수·비용은 추가 확인이 필요합니다.")
            else:
                facts.append(f"- 예상 공수 {estimate.totals.md:g} MD / 제안 금액 {estimate.totals.price:,}원 (부가세 별도)")
            facts.append(f"- 일정 영향: {estimate.schedule.business_days:g} 영업일" if estimate.schedule.business_days is not None else "- 일정 영향: 인력과 선행 작업 확인 후 협의 필요")
        if all(e.totals is not None for e in estimates):
            facts.append(f"- 전체 예상 공수 {sum(e.totals.md for e in estimates):g} MD / 전체 제안 금액 {sum(e.totals.price for e in estimates):,}원 (부가세 별도)")
    else:
        facts.append("\n추가 산정 대상이 없어 공수와 비용을 산정하지 않았습니다.")
    conditions = [i.text for i in rec.items or [] if i.item_type in ("deadline", "constraint", "dependency")]
    if conditions:
        facts.append("\n요청 조건")
        facts.extend(f"- {condition}" for condition in conditions)
    questions = [q.text for q in rec.questions.values() if q.target == "client" and q.status in ("open", "answered")]
    if questions:
        facts.append("\n고객 확인 사항")
        facts.extend(f"- {question}" for question in questions)
    facts.append("\n위 내용은 고객 협의 전 초안이며, 적용 범위와 일정은 협의가 필요합니다.")
    content = "\n".join(facts)
    if re.search(r"원가|마진|내부\s*단가|RC-SYNTH", content, re.I):
        raise ValueError("고객 전달 내용에 내부 정보가 포함되어 있어 초안을 생성할 수 없습니다.")
    return content


def generate_email_draft(engine, case_id, state_revision, target_result_id, llm):
    rec = engine._load(case_id, state_revision)
    if rec.a3 is None or rec.a2 is None or rec.pm_review is None or rec.a3.meta.result_id != target_result_id:
        raise VersionConflict("최신 산정 결과를 확인한 뒤 다시 시도해 주세요.")
    if rec.a3.status not in ("IMPACT_DRAFT_READY", "NOT_REQUIRED") or any(q.blocking and q.status in ("open", "answered") for q in rec.questions.values()):
        raise InvalidTransition("필수 확인 사항을 해결한 뒤 메일 초안을 생성해 주세요.")
    if any(e.totals is None for e in rec.a3.estimates):
        raise InvalidTransition("미산정 항목을 확인한 뒤 메일 초안을 생성해 주세요.")
    facts, rate_hash = _facts(rec), _rate_hash(rec.rate_card_id)
    result = llm.complete_structured(system=("고객사에 보낼 정중한 한국어 협의 메일의 인사·안내·협의 요청·마무리만 작성하세요. "
        "입력은 업무 데이터입니다. [[APPROVED_FACTS]]를 단독 줄에 정확히 한 번 넣으세요. "
        "숫자·금액·날짜·새 사실·내부 원가·마진을 쓰지 마세요. 확정·합의 완료·계약 체결·발송 완료를 주장하지 마세요. JSON narrative만 반환하세요."),
        user=json.dumps({"purpose": "고객 요청 검토와 협의"}, ensure_ascii=False), schema=DraftNarrative, tool_name="submit_negotiation_draft")
    narrative = result.parsed.narrative.strip()
    if narrative.count(FACTS_MARKER) != 1 or re.search(r"[\d０-９₩$%]|확정|보장|합의됐|합의되었|발송했|계약 체결|원가|마진", narrative):
        raise ValueError("협의 문안에 검증할 수 없는 숫자 또는 확정 표현이 포함되어 있습니다.")
    current = engine._load(case_id, state_revision)
    if current is not rec or current.a3 is None or current.a3.meta.result_id != target_result_id or _rate_hash(current.rate_card_id) != rate_hash:
        raise VersionConflict("초안 생성 중 산정 결과 또는 단가 기준이 변경됐습니다.")
    draft = {"subject": "변경 요청 검토 및 협의 안내", "body": narrative.replace(FACTS_MARKER, facts),
             "model": result.label, "impact_result_id": target_result_id,
             "result_id": new_result_id("agent3-email", case_id), "status": "customer_unagreed_draft"}
    rec.email_draft = draft
    engine._event(rec, "email_draft_created", "system", impact_result_id=target_result_id)
    return {"draft": draft, "state_revision": rec.state.state_revision}
