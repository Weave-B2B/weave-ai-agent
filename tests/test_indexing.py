"""색인 1건 처리 (A-MQ-01 → A-MQ-02). 앞단 실패는 DB 없이, 전체 흐름은 VECTOR_TEST_DB_URL이 있을 때 실제 vector-db로."""
import hashlib
import json
import os
import shutil
import uuid

import pytest

from app.core.config import settings
from app.embed.providers import FakeEmbedder
from app.indexing.messages import IndexRequest
from app.indexing.objects import DirStore
from app.indexing.pipeline import index_document, result_keys
from app.kb.loader import chunk_uuid
from app.kb.vector_store import chunk_text, text_hash

PG_URL = os.environ.get("VECTOR_TEST_DB_URL")
PDF = settings.data_dir / "documents" / "CTR-A-001-v1.0.pdf"
needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext 없음")


def _request(**over) -> IndexRequest:
    body = {"requestId": str(uuid.uuid4()), "companyId": str(uuid.uuid4()), "projectId": str(uuid.uuid4()),
            "documentId": str(uuid.uuid4()), "documentVersionId": str(uuid.uuid4()), "fileObjectId": str(uuid.uuid4()),
            "objectKey": "projects/p/documents/ctr.pdf", "mimeType": "application/pdf", **over}
    return IndexRequest.model_validate(body)


@pytest.fixture
def objects(tmp_path):
    store = DirStore(tmp_path)
    store.put("src", "projects/p/documents/ctr.pdf", PDF.read_bytes(), "application/pdf")
    return store


def test_request_reads_backend_event_fields_and_ignores_unknown():
    req = _request(extra="무시")
    assert req.object_key.endswith("ctr.pdf") and req.bucket is None


@pytest.mark.parametrize("over, stage, code, retryable", [
    ({"mimeType": "image/png"}, "EXTRACT", "UNSUPPORTED_MIME_TYPE", False),
    ({"objectKey": "missing.pdf"}, "EXTRACT", "SOURCE_READ_FAILED", True),
    ({"sha256": "0" * 64}, "EXTRACT", "SOURCE_CHECKSUM_MISMATCH", False),
])
def test_early_failures_report_backend_failed_stage(objects, over, stage, code, retryable):
    out = index_document(_request(**over), objects, FakeEmbedder(1024), None, "src", "out").to_json()
    assert (out["status"], out["failedStage"], out["errorCode"], out["retryable"]) == ("FAILED", stage, code, retryable)
    assert "manifest" not in out


def test_dir_store_refuses_path_escape(tmp_path):
    with pytest.raises(ValueError):
        DirStore(tmp_path).get("..", "etc/passwd")


@needs_pdftotext
def test_unparseable_or_formless_documents_fail_without_vectors(tmp_path):
    objects = DirStore(tmp_path)
    objects.put("src", "a.pdf", b"not a pdf", "application/pdf")
    out = index_document(_request(objectKey="a.pdf"), objects, FakeEmbedder(1024), None, "src", "out")
    assert (out.failed_stage, out.error_code) == ("EXTRACT", "EXTRACT_FAILED")


@pytest.fixture
def pg():
    if not PG_URL:
        pytest.skip("VECTOR_TEST_DB_URL 없음")
    from app.kb.vector_store import PgVectorStore, profile_id

    class Emb(FakeEmbedder):
        def __init__(self):
            super().__init__(1024)
            self.model_id = f"fake:index-test-{uuid.uuid4().hex[:8]}"
    emb = Emb()
    store = PgVectorStore(PG_URL, profile_id(emb), 1024)
    store.migrate()
    yield emb, store
    store._conn.execute("DELETE FROM public.chunk_embedding WHERE embedding_profile_id = %s", (store.model_id,))


@needs_pdftotext
def test_ready_writes_vectors_and_verifiable_manifest(objects, pg):
    emb, store = pg
    req = _request(sha256=hashlib.sha256(PDF.read_bytes()).hexdigest())
    out = index_document(req, objects, emb, store, "src", "out")
    msg = out.to_json()
    assert (msg["status"], msg["sectionCount"], msg["extractionVersion"]) == ("READY", 9, "pdftotext-layout-v1")
    assert msg["manifest"]["key"] == result_keys(req.request_id)[1]

    raw = objects.get("out", msg["manifest"]["key"])
    assert hashlib.sha256(raw).hexdigest() == msg["manifest"]["sha256"]          # CN은 받은 바이트 그대로 해시
    assert raw.endswith(b"}\n") and not raw.endswith(b"\n\n") and not raw.startswith(b"\xef\xbb\xbf")
    m = json.loads(raw)
    snap = objects.get("out", m["textSnapshot"]["key"])
    assert hashlib.sha256(snap).hexdigest() == m["textSnapshot"]["sha256"]
    dv = str(req.document_version_id)
    for c in m["chunks"]:
        assert c["chunkId"] == chunk_uuid(dv, c["sectionId"], m["extractionVersion"])     # CN이 재계산해 확인
        assert c["embeddingInputSha256"] == text_hash(chunk_text(c))
        assert "".join(snap.decode()[c["startOffset"]:c["endOffset"]].split()) == "".join(c["text"].split())
    assert store.existing([c["chunkId"] for c in m["chunks"]]).keys() == {c["chunkId"] for c in m["chunks"]}

    again = index_document(req, objects, emb, store, "src", "out").to_json()       # 재수신: 같은 결과
    assert again == msg and objects.get("out", msg["manifest"]["key"]) == raw
