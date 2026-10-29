"""② 결과 → ERD v0.6 저장 행 변환 (ai_analysis · item_evidence · question_answer).

공통 스키마(0.3.0)는 바꾸지 않고 저장할 때만 변환한다. ERD에 없는 필드는 ai_analysis.result(JSONB)에 남는다.
DB에 쓰지는 않는다 — 행 dict를 만들어 BE(또는 저장 담당)에 넘긴다.

ID: ERD는 전부 UUID다. 값이 이미 UUID면 그대로 쓰고, 로컬 문자열 ID(CR-A-001-1 등)는 uuid5로 결정적으로 바꾼다.
운영에서는 BE가 발급한 UUID가 들어오므로 변환이 일어나지 않는다.
"""
import hashlib
import json
import uuid
from typing import Any, Optional

from schema.schemas import Agent2Input, Agent2Output, Evidence

from app.kb.loader import chunk_uuid
from app.orchestration.ids import now_iso

ID_NS = uuid.UUID("0b8e6a4e-4c1f-4f57-8d7e-3c2b9a1f6d20")

# ---- ERD 값 대응 (ERD 원본에 ENUM이 적힌 것만 확정. 나머지는 BE 값 목록을 받으면 고친다) ----
EVIDENCE_ORIGIN_AI = "AI"                                   # 확정: ENUM(AI, MANUAL)
CONFLICT_VERSION = "VERSION_CONFLICT"                       # 확정: ENUM(VERSION_CONFLICT, UNCONFIRMED_COMMITMENT)
CONFLICT_UNCONFIRMED = "UNCONFIRMED_COMMITMENT"
QUESTION_STATUS = {"open": "OPEN", "answered": "ANSWERED"}  # 확정: CHECK(OPEN / ANSWERED). resolved·dismissed는 ERD에 없음
ANALYSIS_TYPE_A2 = "A2"                                     # 미확정: analysis_type 값 목록 없음
ANALYSIS_STATUS = {                                         # 미확정: analysis_status 값 목록 없음 — 지금은 A2 값 그대로
    "READY_FOR_REVIEW": "READY_FOR_REVIEW", "NEEDS_INPUT": "NEEDS_INPUT", "ERROR": "ERROR"}

_EVAL_ONLY = ("gold_origin", "gold_ref")


def to_uuid(kind: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return str(uuid.uuid5(ID_NS, f"{kind}:{value}"))


def input_hash(inp: Agent2Input) -> str:
    """ai_analysis.input_hash: 키 정렬·UTF-8·compact JSON의 SHA-256."""
    raw = json.dumps(inp.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in _EVAL_ONLY}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def sanitize_result(out: Agent2Output) -> dict:
    """ai_analysis.result에 넣을 운영 payload: debug·평가 전용 필드 제거."""
    data = _strip(out.model_dump(mode="json"))
    data["meta"]["debug"] = {}
    data["context"]["scenario_id"] = None
    return data


def chunk_id_of(ev: Evidence) -> str:
    """item_evidence.document_chunk_id: locator 'chunk:<uuid>'에서 꺼내고, 없으면 같은 규칙으로 만든다."""
    if ev.locator and ev.locator.startswith("chunk:"):
        return ev.locator.removeprefix("chunk:")
    return chunk_uuid(ev.doc_id, ev.section_id, ev.extraction_version)


def _conflict(ev: Evidence) -> Optional[str]:
    # 우선하는 변경합의서가 하위 문서를 대체 = 문서 간 상충. UNCONFIRMED_COMMITMENT는 아직 ②가 구조화해 내지 않는다
    return CONFLICT_VERSION if ev.role == "superseding" else None


def to_erd_rows(inp: Agent2Input, out: Agent2Output, *, review_version_id: Optional[str] = None,
                requested_by: Optional[str] = None, retry_count: int = 0,
                created_at: Optional[str] = None) -> dict[str, list[dict]]:
    ctx = out.context
    if out.meta.result_id is None:
        raise ValueError("meta.result_id 필요 — 호출부가 발급한 결과 ID로 ai_analysis_id를 만든다")
    ts = created_at or out.meta.created_at or now_iso()
    analysis_id = to_uuid("ai_analysis", out.meta.result_id)
    rv_id = review_version_id or to_uuid("review_version", f"{ctx.case_id}:{ctx.request_revision}")
    item_uuid = {it.item_id: to_uuid("request_item", f"{ctx.case_id}:{it.item_id}") for it in inp.items}

    def item_ref(item_id: str) -> str:
        return item_uuid.get(item_id) or to_uuid("request_item", f"{ctx.case_id}:{item_id}")

    analysis = {
        "ai_analysis_id": analysis_id, "review_version_id": rv_id, "analysis_type": ANALYSIS_TYPE_A2,
        "model_id": out.meta.model, "prompt_version": out.meta.agent_version, "input_hash": input_hash(inp),
        "analysis_status": ANALYSIS_STATUS[out.status], "result": sanitize_result(out),
        "error_code": out.error.code if out.error else None,
        "retryable": out.error.retryable if out.error else None,     # 실패 시에만 값 존재
        "retry_count": retry_count, "analysis_created_at": ts,
        "completed_at": ts, "requested_by": requested_by,
    }

    evidence, seen = [], set()
    for a in out.assessments:
        for ev in a.evidence:
            row = {
                "item_evidence_id": to_uuid("item_evidence", f"{analysis_id}:{a.item_id}:{chunk_id_of(ev)}"),
                "request_item_id": item_ref(a.item_id), "document_chunk_id": chunk_id_of(ev),
                "ai_analysis_id": analysis_id, "quote": ev.quote, "evidence_origin": EVIDENCE_ORIGIN_AI,
                "conflict_type": _conflict(ev), "location_verified": bool(ev.verified),   # NOT NULL: null → false
                "created_at": ts, "created_by": None,
            }
            key = evidence_key(row)
            if key not in seen:                     # 같은 항목·청크를 두 역할로 인용해도 UNIQUE에 걸리지 않게 1행
                seen.add(key)
                evidence.append(row)

    answers = {a.question_id: a for a in inp.answers}
    questions = []
    for q in out.questions:
        if q.status not in QUESTION_STATUS:
            raise ValueError(f"{q.question_id}: question_status '{q.status}'는 ERD에 없음")
        ans = answers.get(q.question_id) if q.status == "answered" else None
        if q.status == "answered" and ans is None:      # CHECK: ANSWERED면 answer·answered_at NOT NULL
            raise ValueError(f"{q.question_id}: answered인데 입력 answers에 답변 없음")
        questions.append({
            "question_answer_id": to_uuid("question_answer", f"{analysis_id}:{q.question_id}"),
            "review_version_id": rv_id,
            "request_item_id": item_ref(q.item_ids[0]) if q.item_ids else None,   # ERD는 질문당 항목 1개
            "question": q.text, "answer": ans.text if ans else None, "question_status": QUESTION_STATUS[q.status],
            "answered_at": ans.answered_at if ans else None, "created_at": ts,
        })
    return {"ai_analysis": [analysis], "item_evidence": evidence, "question_answer": questions}


def evidence_key(row: dict) -> tuple:
    """item_evidence UNIQUE(request_item_id, document_chunk_id, ai_analysis_id). 저장 시 충돌하면 무시(upsert)."""
    return row["request_item_id"], row["document_chunk_id"], row["ai_analysis_id"]
