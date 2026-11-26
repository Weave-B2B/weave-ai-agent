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
