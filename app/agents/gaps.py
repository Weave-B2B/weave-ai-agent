"""에이전트 결과에서 빠진 정보(InfoGap)를 만든다.

세 에이전트가 같은 규칙으로 gap을 보고하도록 한 곳에 둔다. 총괄 Clarifier도 gaps가 없는
예전 결과에 같은 함수를 써서, 저장된 결과와 새 결과를 똑같이 다룬다.
"""
import hashlib
import json
import re

from schema.schemas import InfoGap, Question

_STAGES = ("agent1", "agent2", "agent3")


def normalize_slot(slot: str | None, fallback: str) -> str:
    """slot을 비교 가능한 이름으로: 앞뒤 공백 제거, 공백은 _로, 소문자."""
    text = re.sub(r"\s+", "_", (slot or "").strip()).casefold()
    return text or fallback


def make_gap_id(stage: str, item_ids, slot: str) -> str:
    key = json.dumps([stage, sorted(item_ids or []), slot], ensure_ascii=False)
    return "gap-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _resolvers(target: str, stage: str = "") -> list[str]:
    """누가 채울 수 있나. Agent 3 정보(기존 기능·판정 기준·규모·진행 상황)는 기준선 문서와 진행 자료로도 채울 수 있다.
    원문 해석(Agent 1, 고객이 원하는 것)은 사람만 답한다. 범위 판단(Agent 2)은 계약 문서로만 채운다."""
    people = ["client"] if target == "client" else ["pm"]
    return people + (["docs", "activity"] if stage == "agent3" else [])


def question_gap(stage: str, q: Question) -> InfoGap:
    slot = normalize_slot(q.field, f"question:{q.question_id}")
    return InfoGap(gap_id=make_gap_id(stage, q.item_ids, slot), stage=stage, item_ids=list(q.item_ids), slot=slot,
                   missing=q.text, impact=q.reason, basis_refs=list(q.basis_refs), blocking=q.blocking,
                   assumption_if_unknown=q.assumption_if_unknown, target=q.target,
                   suggested_options=list(q.choices)[:3], resolvable_by=_resolvers(q.target, stage), question_id=q.question_id)


def gaps_from_output(stage: str, output) -> list[InfoGap]:
    """출력의 gaps가 있으면 그대로, 없으면 질문·범위 판단 불가 항목·Agent 3 needs에서 만든다."""
    if output is None:
        return []
    if getattr(output, "gaps", None):
        return list(output.gaps)
    gaps: dict[str, InfoGap] = {}

    def add(gap: InfoGap):
        gaps.setdefault(gap.gap_id, gap)

    for q in getattr(output, "questions", []) or []:
        if q.status in ("open", "answered"):
            add(question_gap(stage, q))
    if stage == "agent2":
        asked = {i for q in getattr(output, "questions", []) or [] for i in q.item_ids}
        for a in getattr(output, "assessments", []) or []:
            if a.label == "unclear" and a.item_id not in asked:
                # 계약 범위를 판단할 사실이 없다 — 그 항목의 범위 판단을 막는 필수 정보
                add(InfoGap(gap_id=make_gap_id(stage, [a.item_id], "scope_missing"), stage=stage,
                            item_ids=[a.item_id], slot="scope_missing",
                            missing=" / ".join(a.missing_info) or "범위를 판단할 추가 정보",
                            impact=a.rationale or "계약 범위 판단에 필요합니다.",
                            basis_refs=[f"{e.doc_id}:{e.section_id}" for e in a.evidence],
                            blocking=True, resolvable_by=["docs", "pm"], question_id=f"scope-{a.item_id}"))
    if stage == "agent3":
        for need in getattr(output, "needs", []) or []:
            if need.kind not in ("client_input", "missing_standard"):
                continue  # more_evidence는 Agent 2 재조사, not_required는 질문이 아님
            slot = normalize_slot(f"need:{need.kind}:{need.topic}", "need")
            target = "client" if need.target == "client" else "pm"
            add(InfoGap(gap_id=make_gap_id(stage, need.item_ids, slot), stage=stage, item_ids=list(need.item_ids),
                        slot=slot, missing=need.topic, impact=getattr(need, "reason", "") or "영향 산정에 필요합니다.",
                        blocking=need.blocking, assumption_if_unknown=getattr(need, "assumption_if_unknown", None),
                        target=target, suggested_options=list(getattr(need, "choices", []) or [])[:3],
                        resolvable_by=_resolvers(target, stage), question_id=need.question_id))
    return list(gaps.values())


def stage_order(stage: str) -> int:
    return _STAGES.index(stage) if stage in _STAGES else len(_STAGES)
