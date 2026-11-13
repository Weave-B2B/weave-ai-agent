"""KB_SOURCE=backend: 백엔드 기준선·청크를 지식베이스로 읽어 Agent2 문서 도구가 그대로 쓰는지."""
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.kb import backend_kb, loader
from app.kb.backend_kb import BaselineClient, to_kb
from app.tools.doc_tools import get_section, list_baseline_docs, require_active_baseline, search_docs, verify_quote
from app.tools.errors import BaselineSuperseded

PROJECT = str(uuid4())
OLD, NEW = str(uuid4()), str(uuid4())
SOW, CO, REF, DRAFT = (str(uuid4()) for _ in range(4))


def _doc(dv, doc_type, role="BASELINE", approval="APPROVED"):
    return {"documentVersionId": dv, "documentId": str(uuid4()), "documentCode": f"{doc_type[:3]}-001",
            "documentType": doc_type, "documentTitle": doc_type, "documentVersionNo": 1, "approvalStatus": approval,
            "effectiveDate": "2026-09-01", "judgmentRole": role, "extractionStatus": "READY",
            "extractionVersion": "pdftotext-layout-v1"}


def _chunk(dv, sec, heading, text, page=1):
    return {"documentChunkId": str(uuid4()), "documentVersionId": dv, "sectionId": sec, "heading": heading,
            "chunkText": text, "pageStart": page, "pageEnd": page, "lineStart": 3, "lineEnd": 4,
            "startOffset": 10, "endOffset": 10 + len(text), "extractionVersion": "pdftotext-layout-v1"}


def _resp(bl, status, current, docs, chunks):
    return {"baselineId": bl, "projectId": PROJECT, "baselineVersionNo": 1, "baselineStatus": status,
            "baselineEffectiveDate": "2026-09-01", "currentBaselineId": current, "documents": docs, "chunks": chunks}


RESPONSES = {
    OLD: _resp(OLD, "SUPERSEDED", NEW, [_doc(SOW, "SOW")],
               [_chunk(SOW, "SOW-01", "01. 범위", "회원가입과 상품 목록 화면을 구축한다.")]),
    NEW: _resp(NEW, "ACTIVE", NEW,
               [_doc(SOW, "SOW"), _doc(CO, "CHANGE_ORDER"), _doc(REF, "QUOTATION", role="REFERENCE"),
                _doc(DRAFT, "FUNCTIONAL_SPEC", approval="DRAFT")],
               [_chunk(SOW, "SOW-01", "01. 범위", "회원가입과 상품 목록 화면을 구축한다."),
                _chunk(SOW, "SOW-02", "02. 제외 범위", "배송사 API 연동은 범위에서 제외한다.", page=2),
                _chunk(CO, "CO-01", "01. 변경 대상", "배송사 조회 링크 1종 추가를 승인한다."),
                _chunk(REF, "QUO-01", "01. 견적", "배송 연동 견적 300만원")]),
}


@pytest.fixture
def backend(monkeypatch):
    calls = []

    def handle(req):
        calls.append(req)
        bl = req.url.path.split("/")[-2]
        if bl not in RESPONSES:
            return httpx.Response(400, json={"code": "PROJECT_BASELINE_NOT_FOUND"})
        return httpx.Response(200, json=RESPONSES[bl])

    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(handle))
    client = BaselineClient("http://backend", "tok", path=settings.backend_baseline_chunks_path, http=http)
    monkeypatch.setattr(settings, "kb_source", "backend")
    monkeypatch.setattr(backend_kb, "build_client", lambda _s: client)
    loader.clear_remote()
    yield calls
    loader.clear_remote()


def test_to_kb_maps_documents_and_keeps_only_approved_baseline_docs():
    kb = to_kb(RESPONSES[NEW])
    bl = kb["baselines"][NEW]
    assert bl["project_id"] == PROJECT and bl["status"] == "ACTIVE"
    assert set(bl["document_ids"]) == {SOW, CO}                  # 참고·초안 문서는 판단 근거에서 빠진다
    docs = {d["doc_id"]: d for d in kb["documents"]}
    assert docs[CO]["doc_type"] == "change_order" and docs[CO]["priority_rank"] == 1
    assert docs[SOW]["doc_type"] == "sow" and docs[SOW]["priority_rank"] == 3
    sec = next(s for s in kb["sections"] if s["section_id"] == "SOW-02")
    assert sec["page"] == 2 and sec["heading"] == "02. 제외 범위" and sec["ref"] == f"{SOW}:SOW-02"
    assert docs[SOW]["version"] == SOW and sec["doc_version"] == SOW          # 근거 doc_version = 문서 버전 UUID


def test_doc_tools_read_backend_baseline_on_first_lookup(backend):
    docs = list_baseline_docs(NEW)                               # 처음 보는 UUID → 백엔드에서 읽는다
    assert {d["doc_id"] for d in docs["documents"]} == {SOW, CO}
    sec = get_section(SOW, "SOW-02")
    chunk_id = RESPONSES[NEW]["chunks"][1]["documentChunkId"]
    assert sec["text"].startswith("배송사 API") and sec["chunk_id"] == chunk_id and sec["locator"] == f"chunk:{chunk_id}"
    assert verify_quote("배송사 API 연동은 범위에서 제외", SOW, "SOW-02")["verified"] is True
    assert verify_quote("배송 연동 견적", REF, "QUO-01")["verified"] is True     # 조회는 되지만
    hits = search_docs("배송사 연동 범위", NEW, top_k=5)
    assert hits and {h["doc_id"] for h in hits} <= {SOW, CO}                     # 검색은 기준선 승인 문서만
    list_baseline_docs(NEW)
    assert len(backend) == 1                                     # 한 번 읽은 기준선은 다시 부르지 않는다


def test_superseded_backend_baseline_is_refused(backend):
    with pytest.raises(BaselineSuperseded) as e:
        require_active_baseline(OLD)
    assert NEW in str(e.value)
    require_active_baseline(NEW)


def test_unknown_baseline_is_not_found_and_not_refetched(backend):
    missing = str(uuid4())
    assert loader.baselines().get(missing) is None
    assert missing not in loader.baselines()
    assert len(backend) == 1


def test_local_source_never_calls_backend(monkeypatch):
    monkeypatch.setattr(settings, "kb_source", "local")
    monkeypatch.setattr(backend_kb, "build_client", lambda _s: pytest.fail("백엔드 호출 금지"))
    loader.clear_remote()
    assert loader.baselines().get(str(uuid4())) is None
    assert "P-COM-101:BL-1" in loader.baselines()


def test_backend_error_is_raised(monkeypatch):
    http = httpx.Client(base_url="http://b", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="x")))
    client = BaselineClient("http://b", "tok", path=settings.backend_baseline_chunks_path, http=http)
    with pytest.raises(backend_kb.BackendKbError) as e:
        client.fetch(str(uuid4()))
    assert e.value.status_code == 500


def test_refresh_remote_picks_up_documents_added_to_current_baseline(backend, monkeypatch):
    list_baseline_docs(NEW)
    added = str(uuid4())
    changed = {**RESPONSES[NEW], "documents": RESPONSES[NEW]["documents"] + [_doc(added, "CONTRACT")],
               "chunks": RESPONSES[NEW]["chunks"] + [_chunk(added, "CTR-01", "01. 목적", "계약 목적을 정한다.")]}
    monkeypatch.setitem(RESPONSES, NEW, changed)
    assert added not in list_baseline_docs(NEW)["document_ids"]       # 캐시된 기준선
    assert loader.refresh_remote(NEW) is True
    assert added in list_baseline_docs(NEW)["document_ids"]
    assert get_section(added, "CTR-01")["text"] == "계약 목적을 정한다."
