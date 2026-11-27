"""#93 문서 검토(DOC_REVIEW) 워커 작업: 요청 확인·BE 원문 변환·항목 키 변환."""
import httpx
import pytest

from app.agents.doc_review import DocReviewResult, Finding
from app.api import internal_analysis_routes as routes
from app.services import doc_review_job as job

V1, V2, DOC = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _input(**kw):
    base = {"document_review_id": "r1", "project_id": "p1", "as_of": "2026-11-27",
            "source_path": "/internal/v1/document-reviews/r1/source",
            "documents": [{"documentVersionId": V1, "documentId": DOC, "documentVersionNo": 1, "pageCount": 3},
                          {"documentVersionId": V2, "documentId": DOC, "documentVersionNo": 2, "pageCount": 3}]}
    base.update(kw)
    return job.DocReviewJobInput.model_validate(base)


SOURCE = {"asOf": "2026-11-27",
          "documents": [{"documentVersionId": V1, "documentId": DOC, "documentTitle": "계약서", "documentType": "CONTRACT", "documentVersionNo": 1},
                        {"documentVersionId": V2, "documentId": DOC, "documentTitle": "계약서", "documentType": "CONTRACT", "documentVersionNo": 2}],
          "chunks": [{"documentChunkId": "c1", "documentVersionId": V1, "heading": "계약서 > 제3조 검수", "chunkText": "검수 기간은 7일", "pageStart": 1},
                     {"documentChunkId": "c2", "documentVersionId": V2, "heading": "계약서 > 제3조 검수", "chunkText": "검수 기간은 14일", "pageStart": 1}],
          "priorJudgments": [{"changeRequestId": "cr", "crNumber": "CR-1", "requestItemId": "ri", "itemContent": "검수 연장",
                              "label": "out_of_scope", "documentVersionId": V1, "documentChunkId": "c1",
                              "heading": "계약서 > 제3조 검수", "quote": "검수 기간은 7일"}]}


def test_resolve_accepts_doc_review_without_baseline():
    req = routes.AnalysisRequest.model_validate({"aiAnalysisId": "00000000-0000-0000-0000-000000000001",
                                                 "analysisType": "DOC_REVIEW", "input": _input().model_dump()})
    task, inp = routes.resolve(req)
    assert task is routes.run_doc_review and inp.document_review_id == "r1"


@pytest.mark.parametrize("bad", [
    {"documents": []},
    {"documents": [{"documentVersionId": str(i), "documentId": str(i)} for i in range(11)]},
    {"documents": [{"documentVersionId": V1, "documentId": DOC, "pageCount": 201}]},
    {"source_path": "/internal/v1/baselines/x/chunks"},
])
def test_limits_and_source_path_are_rejected(bad):
    req = routes.AnalysisRequest.model_validate({"aiAnalysisId": "00000000-0000-0000-0000-000000000001",
                                                 "analysisType": "DOC_REVIEW", "input": _input(**bad).model_dump()})
    with pytest.raises(routes.InvalidAnalysisRequest):
        routes.resolve(req)


def test_source_to_agent_input_links_versions_and_prior_judgments():
    agent_in = job.to_agent_input(_input(), SOURCE)
    assert [d["version_no"] for d in agent_in.documents] == [1, 2]
    assert {d["document_id"] for d in agent_in.documents} == {DOC}          # 같은 문서 버전은 같은 document_id
    assert agent_in.documents[0]["items"][0] == {"item_id": "c1", "code": "제3조", "heading": "계약서 > 제3조 검수",
                                                  "text": "검수 기간은 7일", "page": 1}
    pj = agent_in.prior_judgments[0]
    assert pj["change_request"] == "CR-1" and pj["evidence"][0]["doc"] == DOC and pj["evidence"][0]["code"] == "제3조"


def test_version_without_chunks_is_failed_document():
    src = dict(SOURCE, chunks=SOURCE["chunks"][:1])
    agent_in = job.to_agent_input(_input(), src)
    assert agent_in.documents[1]["extraction_status"] == "FAILED"


def test_backend_output_converts_keys_to_version_ids():
    agent_in = job.to_agent_input(_input(), SOURCE)
    result = DocReviewResult(findings=[
        Finding(type="CHANGE", docs=[DOC], codes=["제3조"], items=[f"{DOC}@v1#c1", f"{DOC}@v2#c2"], title="검수 기간 변경",
                explanation="7일→14일", confidence="high", change="modified"),
        Finding(type="MISSING", docs=[DOC], codes=[], items=["지어낸키"], title="버려질 후보", explanation="", confidence="low")],
        unchecked=[{"key": f"{DOC}@v2#c2", "document_id": DOC, "code": "제3조", "reason": "x"}],
        coverage={"checked": 1, "total": 2}, trace=[{"big": "x" * 10}], version_diffs=[{"d": 1}])
    out = job.to_backend_output(result, agent_in.documents)
    assert out["findings"] == [{"type": "CHANGE", "items": [f"{V1}@v1#c1", f"{V2}@v2#c2"], "title": "검수 기간 변경",
                                "explanation": "7일→14일", "confidence": "high", "change": "modified", "source": "agent"}]
    assert out["unchecked"][0]["key"] == f"{V2}@v2#c2" and out["unchecked"][0]["document_version_id"] == V2
    assert "trace" not in out and "version_diffs" not in out


def test_fetch_source_errors():
    def client(status):
        return httpx.Client(base_url="http://be", transport=httpx.MockTransport(lambda r: httpx.Response(status, json={})))
    assert job.fetch_source("/internal/v1/document-reviews/r1/source", client(200)) == {}
    with pytest.raises(job.DocReviewSourceError) as e:
        job.fetch_source("/x", client(503))
    assert e.value.retryable
    with pytest.raises(job.DocReviewSourceError) as e:
        job.fetch_source("/x", client(404))
    assert not e.value.retryable


def test_run_doc_review_reports_retrieval_failure(monkeypatch):
    sent = []
    monkeypatch.setattr(routes, "_report", lambda aid, out, failure: sent.append((out, failure)))
    monkeypatch.setattr(job, "fetch_source", lambda path: (_ for _ in ()).throw(job.DocReviewSourceError("down", True)))
    routes.run_doc_review("a1", _input())
    assert sent == [(None, ("RETRIEVAL_UNAVAILABLE", True))]
