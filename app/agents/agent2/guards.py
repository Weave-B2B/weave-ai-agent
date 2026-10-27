"""② 가드 규칙 (코드가 정하는 것): 인용 검증, 기준선 밖 근거 제거, 근거 없음 → unclear 강제."""
from schema.schemas import Agent2Output

from app.kb.loader import baselines, documents
from app.tools.doc_tools import verify_quote

_NEED_EVIDENCE = {"in_scope", "out_of_scope", "approved_change"}


def apply_guards(out: Agent2Output) -> Agent2Output:
    """verify_quote로 Evidence.verified를 채우고, 검증 실패·기준선 밖 근거는 버린다.
    근거가 남지 않은 판단은 unclear로 내리고 이유를 missing_info에 적는다. (공통기준 7장 3·7항)"""
    ctx = out.context
    bl = baselines().get(ctx.baseline_id or "")
    allowed = set(bl["document_ids"]) if bl else set()
    data = out.model_dump()
    for a in data["assessments"]:
        kept, dropped = [], []
        for ev in a["evidence"]:
            in_baseline = ev["doc_id"] in allowed and documents().get(ev["doc_id"], {}).get("project_id") == ctx.project_id
            ok = in_baseline and verify_quote(ev["quote"], ev["doc_id"], ev["section_id"])["verified"]
            (kept if ok else dropped).append({**ev, "verified": True} if ok else ev)
        a["evidence"] = kept
        if dropped:
            a["uncertainties"] = a["uncertainties"] + [f"검증 실패·기준선 밖 근거 {len(dropped)}건 제외"]
        if a["label"] in _NEED_EVIDENCE and (not kept or (a["label"] == "approved_change" and not a["approved_change_ref"])):
            a["label"], a["evidence_level"], a["approved_change_ref"] = "unclear", "none", None
            a["missing_info"] = a["missing_info"] + ["검증된 근거 없음 — 판단 보류"]
    return Agent2Output.model_validate(data)
