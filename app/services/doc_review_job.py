"""문서 검토 분석(DOC_REVIEW) 실행 (#93). BE #127이 큐에 보낸 요청을 받아 문서 검토 에이전트를 돌린다.

요청 input (BE DocumentReviewService.aiInput):
  {document_review_id, project_id, as_of, source_path, documents:[{documentVersionId, documentId, documentTitle,
   documentType, documentSubtype, documentVersionNo, extractionStatus, pageCount}]}
원문(청크)과 관련 기존 AI 판단은 크기가 커서 큐에 없고 source_path(내부 API)로 읽는다.

항목 키: 에이전트는 같은 문서의 버전을 묶으려고 document_id(문서 ID)로 키를 만든다("문서ID@v번호#항목ID").
BE는 "문서버전ID@v번호#항목ID"만 받으므로 결과를 보내기 전에 바꾼다. 항목 ID는 청크 ID다(화면에서 원문 위치로 연결).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.tools.doc_review import item_code

log = logging.getLogger(__name__)

MAX_DOCUMENTS = 10
MAX_PAGES = 200
_KEY = re.compile(r"^(?P<doc>[^@]+)@v(?P<ver>\d+)#(?P<item>.+)$")


class DocReviewJobInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_review_id: str
    project_id: str
    as_of: Optional[str] = None
    source_path: str
    documents: list[dict]


class DocReviewSourceError(RuntimeError):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def check_limits(inp: DocReviewJobInput) -> None:
    """BE가 이미 막지만 워커도 비용 울타리로 한 번 더 확인한다 (ValueError → INVALID_INPUT)."""
    if not inp.documents:
        raise ValueError("검토할 문서가 없습니다.")
    if len(inp.documents) > MAX_DOCUMENTS:
        raise ValueError(f"문서는 {MAX_DOCUMENTS}개까지입니다.")
    if sum(int(d.get("pageCount") or 0) for d in inp.documents) > MAX_PAGES:
        raise ValueError(f"합계 {MAX_PAGES}쪽까지입니다.")
    if not inp.source_path.startswith("/internal/v1/document-reviews/"):
        raise ValueError("source_path가 올바르지 않습니다.")   # 다른 내부 API를 읽게 하지 않는다


def fetch_source(path: str, http: Optional[httpx.Client] = None) -> dict:
    if not settings.backend_base_url and http is None:
        raise DocReviewSourceError("BACKEND_BASE_URL 없음", retryable=False)
    client = http or httpx.Client(base_url=settings.backend_base_url, timeout=max(settings.backend_timeout_sec, 30.0))
    try:
        resp = client.get(path, headers={"X-Internal-Token": settings.backend_internal_token})
    except httpx.TransportError as e:
        raise DocReviewSourceError(f"원문 조회 실패: {type(e).__name__}", retryable=True) from e
    finally:
        if http is None:
            client.close()
    if resp.status_code >= 500:
        raise DocReviewSourceError(f"원문 조회 오류 {resp.status_code}", retryable=True)
    if resp.status_code >= 400:
        raise DocReviewSourceError(f"원문 조회 거부 {resp.status_code}", retryable=False)
    return resp.json()


def to_agent_input(inp: DocReviewJobInput, source: dict):
    """BE 원문 → DocReviewInput. 청크가 없는 문서는 추출 실패로 둔다(에이전트가 미확인으로 표시)."""
    from app.agents.doc_review import DocReviewInput

    by_version: dict[str, list[dict]] = {}
    for c in source.get("chunks", []):
        by_version.setdefault(c["documentVersionId"], []).append(c)
    documents = []
    for d in source.get("documents") or inp.documents:
        chunks = by_version.get(d["documentVersionId"], [])
        items = [{"item_id": c["documentChunkId"], "code": item_code(c.get("heading") or "", c.get("chunkText") or ""),
                  "heading": c.get("heading") or "", "text": c.get("chunkText") or "", "page": c.get("pageStart")}
                 for c in chunks]
        documents.append({"document_id": d["documentId"], "document_version_id": d["documentVersionId"],
                          "title": d.get("documentTitle"), "document_type": d.get("documentType"),
                          "version_no": d.get("documentVersionNo") or 1,
                          "extraction_status": "READY" if items else "FAILED", "items": items})
    return DocReviewInput(as_of=source.get("asOf") or inp.as_of, documents=documents,
                          prior_judgments=prior_judgments(source.get("priorJudgments") or [], documents))


def prior_judgments(refs: list[dict], documents: list[dict]) -> list[dict]:
    """BE 관련 판정(청크마다 한 줄) → 에이전트 모양 {change_request, item, label, evidence:[{doc, code, item_key}]}."""
    doc_of = {d["document_version_id"]: d for d in documents}
    grouped: dict[tuple, dict] = {}
    for r in refs:
        doc = doc_of.get(r.get("documentVersionId"))
        if doc is None:
            continue
        key = (r.get("changeRequestId"), r.get("requestItemId"))
        j = grouped.setdefault(key, {"change_request": r.get("crNumber") or r.get("changeRequestId"),
                                     "change_request_title": r.get("changeRequestTitle"),
                                     "item": r.get("itemContent"), "label": r.get("label"), "evidence": []})
        j["evidence"].append({"doc": doc["document_id"], "code": item_code(r.get("heading") or "", r.get("quote") or ""),
                              "item_key": f"{doc['document_id']}@v{doc['version_no']}#{r.get('documentChunkId')}",
                              "quote": (r.get("quote") or "")[:200]})
    return list(grouped.values())


def to_backend_output(result, documents: list[dict]) -> dict:
    """에이전트 결과 → BE 결과. 항목 키의 문서 ID를 문서 버전 ID로 바꾸고, 큰 실행 기록(trace·version_diffs)은 뺀다."""
    version_of = {(d["document_id"], int(d["version_no"])): d["document_version_id"] for d in documents}

    def convert(key: str) -> Optional[str]:
        m = _KEY.match(key or "")
        if not m:
            return None
        vid = version_of.get((m["doc"], int(m["ver"])))
        return f"{vid}@v{m['ver']}#{m['item']}" if vid else None

    findings = []
    for f in result.findings:
        keys = [k for k in (convert(k) for k in f.items) if k]
        if not keys and f.type != "UNCHECKED":
            log.warning("항목 키를 바꾸지 못한 후보를 뺌: %s", f.title[:60])
            continue
        findings.append({"type": f.type, "items": keys, "title": f.title, "explanation": f.explanation,
                         "confidence": f.confidence, "change": f.change, "source": f.source})
    unchecked = []
    for u in result.unchecked:
        u = dict(u)
        ver = u.get("version_no")
        if "key" in u:
            m = _KEY.match(u["key"] or "")
            ver = ver or (int(m["ver"]) if m else None)
            u["key"] = convert(u["key"]) or u["key"]
        u["document_version_id"] = version_of.get((u.get("document_id"), int(ver or 1)))
        unchecked.append(u)
    return {"agent_version": result.agent_version, "findings": findings, "unchecked": unchecked,
            "coverage": result.coverage, "usage": result.usage}


def run(inp: DocReviewJobInput) -> dict:
    from app.agents.doc_review import DocReviewAgent
    from app.llm.factory import build_llm

    check_limits(inp)
    source = fetch_source(inp.source_path)
    agent_input = to_agent_input(inp, source)
    result = DocReviewAgent(build_llm(settings)).run(agent_input)
    return to_backend_output(result, agent_input.documents)
