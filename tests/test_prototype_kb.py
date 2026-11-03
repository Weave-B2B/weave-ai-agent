"""폐기된 기준선 거절과 프로토타입 문서 추가·새 기준선 흐름. 업로드는 VECTOR_TEST_DB_URL이 있을 때 실제 vector-db로."""
import base64
import os
import shutil
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.fixture import FixtureAgent2, load_chain
from app.core.config import settings
from app.embed.providers import FakeEmbedder
from app.kb import loader
from app.tools import doc_tools
from app.tools.errors import BaselineSuperseded
from schema.schemas import Agent2Input

PG_URL = os.environ.get("VECTOR_TEST_DB_URL")
PDF = settings.data_dir / "documents" / "SOW-A-001-v1.0.pdf"


def _a2_input(baseline_id: str) -> Agent2Input:
    gold = load_chain()["CR-A-001"]["agent2_gold"]
    ctx = {**gold["context"], "baseline_id": baseline_id}
    return Agent2Input(context=ctx, message_text="리뷰 기능", items=[], constraints=[])


def test_superseded_baseline_is_rejected_for_new_judgment():
    assert loader.superseded_by("P-COM-101:BL-1") == "P-COM-101:BL-2"
    assert loader.superseded_by("P-COM-101:BL-2") is None
    doc_tools.require_active_baseline("P-COM-101:BL-2")
    with pytest.raises(BaselineSuperseded, match="P-COM-101:BL-2"):
        doc_tools.require_active_baseline("P-COM-101:BL-1")
    with pytest.raises(BaselineSuperseded):
        FixtureAgent2(load_chain()).run(_a2_input("P-COM-101:BL-1"))
    # 검색 도구는 과거 기준선도 조회한다 (재현·감사용)
    assert doc_tools.search_docs("배송사 조회 링크", "P-COM-101:BL-1", top_k=3)


def test_agent2_api_returns_422_for_superseded_baseline():
    from app.api.agent2_routes import router
    app = FastAPI(); app.include_router(router)
    body = _a2_input("P-COM-101:BL-1").model_dump(mode="json")
    res = TestClient(app).post("/agent2/assessments", json=body)
    assert res.status_code == 422 and "BASELINE_SUPERSEDED" in res.text


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOTYPE_LAB", "1")
    monkeypatch.setattr(loader, "lab_kb_path", lambda: tmp_path / ".prototype-kb" / "kb.json")
    loader.clear_caches()
    from prototype.lab import router
    app = FastAPI(); app.include_router(router)
    yield TestClient(app)
    loader.clear_caches()


def test_projects_lists_dataset_baselines_with_status(lab):
    body = lab.get("/prototype-lab/kb/projects").json()
    p = next(p for p in body["projects"] if p["project_id"] == "P-COM-101")
    status = {b["baseline_id"]: b["status"] for b in p["baselines"]}
    assert status == {"P-COM-101:BL-1": "SUPERSEDED", "P-COM-101:BL-2": "ACTIVE"} and p["active_baseline_id"] == "P-COM-101:BL-2"


def test_upload_needs_vector_db(lab):
    res = lab.post("/prototype-lab/kb/projects/P-COM-101/documents",
                   json={"filename": "a.pdf", "doc_type": "sow", "content_base64": base64.b64encode(b"x").decode()})
    assert res.status_code == 409


@pytest.fixture
def pg(monkeypatch):
    if not PG_URL or shutil.which("pdftotext") is None:
        pytest.skip("VECTOR_TEST_DB_URL 또는 pdftotext 없음")
    from app.kb.vector_store import PgVectorStore, profile_id

    class Emb(FakeEmbedder):
        def __init__(self):
            super().__init__(1024)
            self.model_id = f"fake:lab-kb-test-{uuid.uuid4().hex[:8]}"
    emb = Emb()
    store = PgVectorStore(PG_URL, profile_id(emb), 1024)
    store.migrate()
    backend = lambda: (emb, store)                                         # noqa: E731
    backend.cache_clear = lambda: None                                     # conftest가 정리 때 부른다
    monkeypatch.setattr(doc_tools, "vector_backend", backend)
    monkeypatch.setattr(doc_tools, "_query_vector", lambda q: tuple(emb.embed([q])[0]))
    yield
    store._conn.execute("DELETE FROM public.chunk_embedding WHERE embedding_profile_id = %s", (store.model_id,))


def test_upload_index_and_new_baseline_supersedes_old(lab, pg):
    up = lab.post("/prototype-lab/kb/projects/P-COM-101/documents",
                  json={"filename": "과업지시서-개정.pdf", "doc_type": "sow", "content_base64": base64.b64encode(PDF.read_bytes()).decode()})
    assert up.status_code == 200, up.text
    result, doc = up.json()["result"], up.json()["document"]
    assert result["status"] == "READY" and doc["section_count"] == result["sectionCount"] > 0
    doc_id = doc["doc_id"]
    assert (doc_id, doc["sections"][0]["section_id"]) in loader.sections() and loader.documents()[doc_id]["title"] == "과업지시서-개정"

    bad = lab.post("/prototype-lab/kb/projects/P-COM-102/baselines", json={"document_ids": [doc_id]})
    assert bad.status_code == 422                                          # 다른 프로젝트 문서는 넣지 않는다
    docs = loader.baselines()["P-COM-101:BL-2"]["document_ids"] + [doc_id]
    bl = lab.post("/prototype-lab/kb/projects/P-COM-101/baselines", json={"document_ids": docs}).json()
    assert bl["baseline_id"] == "P-COM-101:BL-3" and bl["supersedes"] == "P-COM-101:BL-2"

    hits = doc_tools.search_docs("수행 범위", "P-COM-101:BL-3", top_k=20, filters={"doc_types": ["sow"]})
    assert doc_id in {h["doc_id"] for h in hits}
    assert doc_id not in {h["doc_id"] for h in doc_tools.search_docs("수행 범위", "P-COM-101:BL-2", top_k=20)}
    with pytest.raises(BaselineSuperseded, match="P-COM-101:BL-3"):
        doc_tools.require_active_baseline("P-COM-101:BL-2")
    assert lab.delete(f"/prototype-lab/kb/documents/{doc_id}").status_code == 409    # 기준선에 들어간 문서

    assert lab.post("/prototype-lab/kb/reset").json() == {"reset": True}
    assert "P-COM-101:BL-3" not in loader.baselines() and doc_id not in loader.documents()
    assert loader.superseded_by("P-COM-101:BL-2") is None


def test_formless_pdf_reports_failure_and_is_not_added(lab, pg):
    up = lab.post("/prototype-lab/kb/projects/P-COM-101/documents",
                  json={"filename": "x.pdf", "doc_type": "sow", "content_base64": base64.b64encode(b"not a pdf").decode()})
    assert up.json()["result"]["errorCode"] == "EXTRACT_FAILED" and up.json()["document"] is None
    assert not any(d.get("version") == "lab" for d in loader.documents().values())


def test_evidence_source_lines_mark_the_quoted_range_with_context():
    from prototype.lab import router
    app = FastAPI()
    app.include_router(router)
    url = "/prototype-lab/kb/documents/SOW-A-001-v1.0/lines"
    with TestClient(app) as client:
        body = client.get(url, params={"page": 1, "start": 44, "end": 46}).json()
        assert [line["no"] for line in body["lines"]] == list(range(41, 50))
        assert [line["no"] for line in body["lines"] if line["hit"]] == [44, 45, 46]
        assert "최초 제외 기능" in body["lines"][3]["text"]
        assert client.get(url, params={"page": 1, "start": 1, "end": 1, "context": 0}).json()["lines"][0]["no"] == 1
        assert client.get(url, params={"page": 9, "start": 1, "end": 1}).status_code == 422
        assert client.get(url, params={"page": 1, "start": 46, "end": 44}).status_code == 422
        assert client.get("/prototype-lab/kb/documents/NOPE/lines", params={"page": 1, "start": 1, "end": 1}).status_code == 404


def _lab_doc(project_id: str, doc_id: str, doc_type: str) -> dict:
    return {"doc_id": doc_id, "project_id": project_id, "doc_type": doc_type, "version": "lab", "title": doc_id}


def test_new_project_is_draft_until_first_baseline_with_contract_or_sow(lab):
    from prototype import kb_routes
    base = "/prototype-lab/kb/projects"
    assert lab.post(base, json={"project_id": "p-new", "name": "x"}).status_code == 422
    assert lab.post(base, json={"project_id": "P-COM-101", "name": "중복"}).status_code == 409
    assert lab.post(base, json={"project_id": "P-NEW-1", "name": "신규"}).status_code == 200
    p = next(p for p in lab.get(base).json()["projects"] if p["project_id"] == "P-NEW-1")
    assert p["draft"] and p["name"] == "신규" and p["documents"] == [] and p["active_baseline_id"] is None
    # 작성 중 프로젝트에도 업로드할 수 있다 (vector-db가 없으면 409, 알 수 없는 프로젝트 404가 아니다)
    up = lab.post(f"{base}/P-NEW-1/documents", json={"filename": "a.pdf", "doc_type": "sow", "content_base64": base64.b64encode(b"x").decode()})
    assert up.status_code == 409

    data = kb_routes._load()
    data["documents"] += [_lab_doc("P-NEW-1", "q", "quote"), _lab_doc("P-NEW-1", "co", "change_order"), _lab_doc("P-NEW-1", "s", "sow")]
    kb_routes._save(data)
    assert lab.post(f"{base}/P-NEW-1/baselines", json={"document_ids": ["q"]}).status_code == 422          # 계약서·과업지시서 없음
    assert lab.post(f"{base}/P-NEW-1/baselines", json={"document_ids": ["s", "co"]}).status_code == 422    # 변경합의서는 나중에
    assert lab.post(f"{base}/P-NEW-1/baselines", json={"document_ids": ["s"], "as_of": "2999-01-01"}).status_code == 422
    first = lab.post(f"{base}/P-NEW-1/baselines", json={"document_ids": ["s", "q"], "as_of": "2026-09-01"}).json()
    assert first["baseline_id"] == "P-NEW-1:BL-1" and first["as_of"] == "2026-09-01" and first["supersedes"] is None
    p = next(p for p in lab.get(base).json()["projects"] if p["project_id"] == "P-NEW-1")
    assert not p["draft"] and p["active_baseline_id"] == "P-NEW-1:BL-1"
    assert lab.post(base, json={"project_id": "P-NEW-1", "name": "다시"}).status_code == 409
    # 이후 기준선은 변경합의서를 넣을 수 있고 기준일은 오늘이다
    assert lab.post(f"{base}/P-NEW-1/baselines", json={"document_ids": ["s"], "as_of": "2026-09-02"}).status_code == 422
    assert lab.post(f"{base}/P-NEW-1/baselines", json={"document_ids": ["s", "q", "co"]}).json()["supersedes"] == "P-NEW-1:BL-1"
