from uuid import uuid4

import httpx
import pytest

from app.indexing.backend import DEFAULT_PATH, IngestReportError, IngestResultClient
from app.indexing.messages import IndexResult


def _client(handler, path=DEFAULT_PATH):
    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(handler))
    return IngestResultClient("http://backend", "tok", path=path, http=http)


def _ready(dv):
    return IndexResult(status="READY", request_id=uuid4(), extraction_version="pdftotext-layout-v1",
                       section_count=12, document_version_id=dv)


def test_report_posts_a_mq_02_with_token():
    dv, seen = uuid4(), []

    def handle(req):
        seen.append(req)
        return httpx.Response(200, json={"processingStatus": "READY"})

    out = _client(handle).report(dv, _ready(dv))
    assert out == {"processingStatus": "READY"}
    req = seen[0]
    assert req.method == "POST"
    assert req.url.path == f"/internal/v1/document-versions/{dv}/ingest-result"
    assert req.headers["X-Internal-Token"] == "tok"
    body = httpx.Response(200, content=req.content).json()
    assert body["status"] == "READY" and body["sectionCount"] == 12 and "requestId" in body


def test_path_override_for_local_dev():
    dv, seen = uuid4(), []
    client = _client(lambda r: seen.append(r) or httpx.Response(200, json={}),
                     path="/dev/document-versions/{document_version_id}/ingest-result")
    client.report(dv, _ready(dv))
    assert seen[0].url.path == f"/dev/document-versions/{dv}/ingest-result"


@pytest.mark.parametrize("code,retryable", [(500, True), (503, True), (429, True), (400, False), (401, False), (404, False)])
def test_error_classification(code, retryable):
    dv = uuid4()
    with pytest.raises(IngestReportError) as e:
        _client(lambda r: httpx.Response(code, text="x")).report(dv, _ready(dv))
    assert e.value.retryable is retryable and e.value.status_code == code


def test_transport_error_is_retryable():
    def boom(req):
        raise httpx.ConnectError("down", request=req)

    dv = uuid4()
    with pytest.raises(IngestReportError) as e:
        _client(boom).report(dv, _ready(dv))
    assert e.value.retryable is True


def test_path_must_contain_placeholder():
    with pytest.raises(ValueError):
        IngestResultClient("http://backend", "tok", path="/internal/v1/ingest")
