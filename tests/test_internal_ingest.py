"""백엔드 → AI 문서 색인 요청 입구와 결과 전달 (POST /internal/v1/document-ingests)."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import internal_ingest_routes
from app.core.config import settings
from app.indexing.messages import IndexResult
from app.main import app

TOKEN = "tok"


class Recorder:
    def __init__(self):
        self.calls = []

    def report(self, document_version_id, result):
        self.calls.append((document_version_id, result))
        return {}

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(settings, "backend_internal_token", TOKEN)
    monkeypatch.setattr(internal_ingest_routes, "build_client", lambda _s: rec)
    monkeypatch.setattr(internal_ingest_routes, "REPORT_BACKOFF_SEC", 0)
    return TestClient(app), rec


def _body():
    return {"requestId": str(uuid4()), "projectId": str(uuid4()), "documentVersionId": str(uuid4()),
            "objectKey": "projects/p/documents/d.pdf", "mimeType": "application/pdf"}


def test_token_required(env):
    client, rec = env
    assert client.post("/internal/v1/document-ingests", json=_body()).status_code == 401
    assert rec.calls == []


def test_accepts_and_reports_result(env, monkeypatch):
    client, rec = env
    body = _body()

    def fake_run(req):
        internal_ingest_routes._report(req.document_version_id, IndexResult(status="READY", request_id=req.request_id, section_count=3))
    monkeypatch.setattr(internal_ingest_routes, "run_ingest", fake_run)

    resp = client.post("/internal/v1/document-ingests", json=body, headers={"X-Internal-Token": TOKEN})

    assert resp.status_code == 202
    assert resp.json()["status"] == "ACCEPTED"
    assert [(str(v), r.status) for v, r in rec.calls] == [(body["documentVersionId"], "READY")]


def test_setup_failure_is_reported_as_failed(env, monkeypatch):
    client, rec = env
    monkeypatch.setattr(settings, "embed_provider", "none")

    resp = client.post("/internal/v1/document-ingests", json=_body(), headers={"X-Internal-Token": TOKEN})

    assert resp.status_code == 202
    result = rec.calls[0][1]
    assert (result.status, result.failed_stage, result.retryable) == ("FAILED", "EMBED", True)


def test_vector_schema_is_created_once_per_db(monkeypatch):
    """색인이 vector-db를 열 때 확장·테이블을 만든다 (IF NOT EXISTS). 같은 DB는 프로세스마다 한 번만."""
    calls = []

    class Store:
        def migrate(self):
            calls.append(settings.vector_db_url)

    monkeypatch.setattr(internal_ingest_routes, "_schema_ready", set())
    monkeypatch.setattr(settings, "vector_db_url", "postgresql://a")
    internal_ingest_routes._ensure_schema(Store())
    internal_ingest_routes._ensure_schema(Store())
    monkeypatch.setattr(settings, "vector_db_url", "postgresql://b")
    internal_ingest_routes._ensure_schema(Store())
    assert calls == ["postgresql://a", "postgresql://b"]


def test_schema_failure_is_reported_as_failed_and_retried_next_time(env, monkeypatch):
    """권한 부족 등으로 테이블을 못 만들면 색인을 FAILED로 알리고, 다음 색인에서 다시 시도한다."""
    import psycopg

    client, rec = env
    attempts = []

    class Store:
        def migrate(self):
            attempts.append(1)
            raise psycopg.errors.InsufficientPrivilege("permission denied to create extension")

    monkeypatch.setattr(internal_ingest_routes, "_schema_ready", set())
    monkeypatch.setattr(settings, "embed_provider", "fake")
    monkeypatch.setattr("app.kb.vector_store.open_pg_store", lambda *_: Store())
    for _ in range(2):
        assert client.post("/internal/v1/document-ingests", json=_body(), headers={"X-Internal-Token": TOKEN}).status_code == 202
    assert [(r.status, r.error_code, r.retryable) for _, r in rec.calls] == [("FAILED", "INDEXING_UNAVAILABLE", True)] * 2
    assert len(attempts) == 2
    assert "InsufficientPrivilege" in rec.calls[0][1].error_message


def test_r2_parser_in_shared_ingest_reuses_chunk_ids_and_legacy_switch(env, monkeypatch, tmp_path):
    import json

    from app.embed.providers import FakeEmbedder
    from app.indexing import pdf_r2
    from app.indexing.objects import DirStore
    from app.kb.loader import chunk_uuid

    client, rec = env
    body = _body()
    objects = DirStore(tmp_path)
    objects.put(settings.s3_source_bucket, body["objectKey"], b"fake PDF", "application/pdf")
    monkeypatch.setattr(settings, "pdf_parser", "r2")
    monkeypatch.setattr(settings, "result_transport", "http")
    monkeypatch.setattr("app.embed.build_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("app.indexing.objects.S3Store", lambda *args: objects)

    class Store:
        model_id = "test-pgvector"

        def __init__(self):
            self.rows = {}
            self.insert_calls = 0

        def migrate(self):
            pass

        def existing(self, ids):
            return {cid: self.rows[cid] for cid in ids if cid in self.rows}

        def insert(self, rows):
            self.insert_calls += 1
            for cid, version_id, project_id, _, digest, version in rows:
                self.rows[cid] = (version_id, project_id, digest, version)

    store = Store()
    monkeypatch.setattr("app.kb.vector_store.open_pg_store", lambda *_: store)
    monkeypatch.setattr(pdf_r2, "extract_pages", lambda *_: (["첫 페이지", "검수 조건"], [{"page": 2, "reasons": ["scan"]}]))
    cid = chunk_uuid(body["documentVersionId"], "B1-00001", pdf_r2.EXTRACTION_VERSION)
    chunk = dict(chunk_id=cid, extraction_version=pdf_r2.EXTRACTION_VERSION, section_id="B1-00001", heading="[계약 > 검수]", text="검수 조건",
                 page=2, page_end=2, line_start=1, line_end=1, start_offset=6, end_offset=11,
                 feature_id=None, scope_flag=None)
    monkeypatch.setattr(pdf_r2, "build_chunks", lambda _, pages: (pages, [chunk]))

    for _ in range(2):
        assert client.post("/internal/v1/document-ingests", json=body, headers={"X-Internal-Token": TOKEN}).status_code == 202
    first, second = (result for _, result in rec.calls)
    assert first.status == second.status == "READY"
    assert first.manifest == second.manifest
    assert store.insert_calls == 1
    assert store.rows[cid][:2] == (body["documentVersionId"], body["projectId"])
    manifest = json.loads(objects.get(first.manifest.bucket, first.manifest.key))
    assert manifest["extractionVersion"] == pdf_r2.EXTRACTION_VERSION
    assert manifest["pageFallbacks"] == [{"page": 2, "reasons": ["scan"]}]
    assert manifest["chunks"][0]["pageStart"] == manifest["chunks"][0]["pageEnd"] == 2

    monkeypatch.setattr(settings, "pdf_parser", "legacy")          # 되돌리면 기존 파서 버전으로 색인
    body.update(projectId=str(uuid4()), requestId=str(uuid4()))
    assert client.post("/internal/v1/document-ingests", json=body, headers={"X-Internal-Token": TOKEN}).status_code == 202
    assert rec.calls[-1][1].extraction_version == "pdftotext-layout-v1"
    assert store.insert_calls == 1
