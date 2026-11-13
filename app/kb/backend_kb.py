"""백엔드 기준선을 지식베이스로 읽는다 (KB_SOURCE=backend).

백엔드 내부 API(GET /internal/v1/baselines/{baselineId}/chunks)에서 기준선 문서와 AI 색인 청크 원문을 받아
loader.sections()·documents()·baselines()와 같은 모양으로 바꾼다. 청크 ID는 색인 때 만든 UUID 그대로라
pgvector(chunk_embedding) 검색 결과와 바로 이어진다. doc_id는 문서 버전 UUID다.
"""
from __future__ import annotations

import uuid
from typing import Optional

import httpx

from app.indexing.backend import TOKEN_HEADER

# 백엔드 DocumentType → Agent2 doc_type. 선언 순서 = 기준선 문서 우선순위 (백엔드 enum과 같음)
DOC_TYPES = {
    "CHANGE_ORDER": ("change_order", "CO"),
    "CONTRACT": ("contract", "CTR"),
    "SOW": ("sow", "SOW"),
    "FUNCTIONAL_SPEC": ("functional_spec", "FSP"),
    "QUOTATION": ("quote", "QUO"),
    "APPROVAL_EVIDENCE": ("approval_evidence", "APV"),
    "OTHER": ("other", "DOC"),
}
PRIORITY = [code for _, code in DOC_TYPES.values()]


class BackendKbError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def is_backend_id(baseline_id: str) -> bool:
    try:
        uuid.UUID(baseline_id)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class BaselineClient:
    def __init__(self, base_url: str, service_token: str, *, path: str, timeout: float = 10.0,
                 http: Optional[httpx.Client] = None):
        if "{baseline_id}" not in path:
            raise ValueError("path에 {baseline_id}가 있어야 합니다.")
        self._path = path
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {TOKEN_HEADER: service_token}

    def fetch(self, baseline_id: str) -> Optional[dict]:
        """기준선 응답. 없는 기준선이면 None."""
        try:
            resp = self._http.get(self._path.format(baseline_id=baseline_id), headers=self._headers)
        except httpx.TransportError as e:
            raise BackendKbError(f"백엔드 기준선 조회 실패: {type(e).__name__}") from e
        if resp.status_code == 400 and "BASELINE_NOT_FOUND" in resp.text:
            return None
        if resp.status_code >= 400:
            raise BackendKbError(f"백엔드 기준선 조회 오류 {resp.status_code}: {resp.text[:300]}",
                                 status_code=resp.status_code)
        return resp.json()


def build_client(settings) -> BaselineClient:
    if not settings.backend_base_url:
        raise BackendKbError("KB_SOURCE=backend에는 BACKEND_BASE_URL이 필요합니다.")
    return BaselineClient(settings.backend_base_url, settings.backend_internal_token,
                          path=settings.backend_baseline_chunks_path, timeout=settings.backend_timeout_sec)


def to_kb(resp: dict) -> dict:
    """백엔드 응답 → {"baselines": {...}, "documents": [...], "sections": [...]} (loader 모양).
    판단 기준은 승인된 BASELINE 역할 문서만이다. 참고(REFERENCE)·초안 문서는 근거 후보에 넣지 않는다."""
    bl_id, project_id = resp["baselineId"], resp["projectId"]
    docs, doc_ids = [], []
    by_version: dict[str, dict] = {}
    for d in resp.get("documents", []):
        doc_type, code = DOC_TYPES.get(d["documentType"], ("other", "DOC"))
        doc = {"doc_id": d["documentVersionId"], "project_id": project_id, "doc_type": doc_type,
               "version": str(d["documentVersionNo"]), "effective_date": d.get("effectiveDate"),
               "approval_state": (d.get("approvalStatus") or "").lower(), "priority_rank": PRIORITY.index(code) + 1,
               "title": d.get("documentTitle"), "document_code": d.get("documentCode"),
               "judgment_role": d.get("judgmentRole"), "extraction_status": d.get("extractionStatus")}
        by_version[doc["doc_id"]] = doc
        docs.append(doc)
        if d.get("judgmentRole") == "BASELINE" and d.get("approvalStatus") == "APPROVED":
            doc_ids.append(doc["doc_id"])
    secs = []
    for c in resp.get("chunks", []):
        doc = by_version.get(c["documentVersionId"])
        if doc is None or not c.get("sectionId"):
            continue
        secs.append({
            "doc_id": doc["doc_id"], "section_id": c["sectionId"], "project_id": project_id,
            "doc_type": doc["doc_type"], "doc_version": doc["version"], "approval_state": doc["approval_state"],
            "effective_date": doc["effective_date"], "priority_rank": doc["priority_rank"],
            "heading": c.get("heading") or "", "text": c["chunkText"],
            "page": c.get("pageStart"), "line_start": c.get("lineStart"), "page_end": c.get("pageEnd"),
            "line_end": c.get("lineEnd"), "feature_id": None, "scope_flag": None,
            "ref": f"{doc['doc_id']}:{c['sectionId']}", "locator": None,
            "extraction_version": c.get("extractionVersion"), "chunk_id": c["documentChunkId"],
            "start_offset": c.get("startOffset"), "end_offset": c.get("endOffset")})
    for doc in docs:
        doc["section_count"] = sum(1 for s in secs if s["doc_id"] == doc["doc_id"])
    baseline = {"project_id": project_id, "as_of": resp.get("baselineEffectiveDate"), "document_ids": doc_ids,
                "priority": PRIORITY[:5], "status": resp.get("baselineStatus"),
                "version_no": resp.get("baselineVersionNo")}
    return {"baselines": {bl_id: baseline}, "documents": docs, "sections": secs}


def load_baseline(baseline_id: str, client: BaselineClient) -> Optional[dict]:
    """기준선(과 대체됐으면 현재 기준선)을 loader 모양으로 읽는다. 현재 기준선에 supersedes를 달아
    loader.superseded_by가 로컬과 똑같이 '대체된 기준선'을 알아보게 한다."""
    resp = client.fetch(baseline_id)
    if resp is None:
        return None
    kb = to_kb(resp)
    current = resp.get("currentBaselineId")
    if resp.get("baselineStatus") == "SUPERSEDED" and current and current != baseline_id:
        cur = client.fetch(current)
        if cur is not None:
            ckb = to_kb(cur)
            ckb["baselines"][current]["supersedes"] = baseline_id
            kb["baselines"].update(ckb["baselines"])
            kb["documents"] += ckb["documents"]
            kb["sections"] += ckb["sections"]
    return kb
