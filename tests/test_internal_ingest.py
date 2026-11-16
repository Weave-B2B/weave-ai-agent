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
