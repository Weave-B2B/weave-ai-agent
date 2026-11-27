"""질문·선택지·가정의 근거를 출처 종류와 함께 다룬다.

에이전트는 근거를 문자열(basis_refs)로 남긴다. 여기서 출처 종류(request·contract·activity)를
붙인 EvidenceRef로 바꾸고, 범위 판단처럼 계약 근거만 허용되는 곳을 검사한다.
"""
import re
from collections.abc import Iterable

from schema.schemas import EvidenceRef

_MESSAGE_SPAN = re.compile(r"^message_text\[(\d+):(\d+)\]$")
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_GITHUB_PR = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
_GITHUB_SHA = re.compile(r"^(?:[\w.-]+/[\w.-]+@)?[0-9a-f]{7,40}$")
_DOC_SECTION = re.compile(r"^([^:\s]+):(\S.*)$")


def _clip(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def baseline_doc_titles(baseline_id: str | None) -> dict[str, str]:
    """기준선 문서 ID → 제목. 기준선을 모르면 빈 값 (제목 없이 ID로 표시)."""
    if not baseline_id:
        return {}
    try:
        from app.tools.doc_tools import list_baseline_docs
        return {d["doc_id"]: d.get("title") or d["doc_id"] for d in list_baseline_docs(baseline_id)["documents"]}
    except Exception:  # 근거 표기는 보조 정보다 — 조회 실패로 질문 흐름을 막지 않는다
        return {}


def parse_basis_ref(ref: str, *, message_text: str | None = None,
                    doc_titles: dict[str, str] | None = None) -> EvidenceRef | None:
    """에이전트가 남긴 근거 문자열을 EvidenceRef로 바꾼다. 형식을 모르면 None (원문 문자열은 basis_refs에 남는다)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if span := _MESSAGE_SPAN.match(ref):
        start, end = int(span[1]), int(span[2])
        quote = message_text[start:end].strip() if message_text and 0 <= start < end <= len(message_text) else ""
        return EvidenceRef(source_type="request", system="message", ref=ref, title=_clip(quote) if quote else None)
    if _JIRA_KEY.match(ref):
        return EvidenceRef(source_type="activity", system="jira", ref=ref)
    if _GITHUB_PR.match(ref) or _GITHUB_SHA.match(ref):
        return EvidenceRef(source_type="activity", system="github", ref=ref)
    if cite := _DOC_SECTION.match(ref):
        doc_id = cite[1]
        if doc_titles and doc_id not in doc_titles:
            return None  # 기준선 목록을 알 때, 거기 없는 문서는 계약 근거로 올리지 않는다
        title = f"{(doc_titles or {}).get(doc_id, doc_id)} · {cite[2]}"
        return EvidenceRef(source_type="contract", system="doc", ref=ref, title=title)
    return None


def parse_basis_refs(refs: Iterable[str], **context) -> list[EvidenceRef]:
    """여러 근거를 변환한다. 같은 ref는 한 번만 남긴다."""
    seen, result = set(), []
    for ref in refs or []:
        parsed = parse_basis_ref(ref, **context)
        if parsed and parsed.ref not in seen:
            seen.add(parsed.ref)
            result.append(parsed)
    return result


class NonContractEvidence(ValueError):
    """계약 근거만 허용되는 판단에 다른 출처의 근거가 들어왔다."""


def require_contract(refs: Iterable[EvidenceRef], purpose: str = "범위 판단") -> list[EvidenceRef]:
    """범위 판단·계약 인용에는 기준선 문서(contract)만 쓴다. 진행 자료(Jira·GitHub)나 원문은 거절한다."""
    refs = list(refs)
    others = [r.ref for r in refs if r.source_type != "contract"]
    if others:
        raise NonContractEvidence(f"{purpose}에는 계약 문서 근거만 쓸 수 있습니다: {', '.join(others)}")
    return refs


def contract_only(refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    """계약 근거만 남긴다 (거절 대신 걸러야 하는 곳에서 사용)."""
    return [r for r in refs if r.source_type == "contract"]
