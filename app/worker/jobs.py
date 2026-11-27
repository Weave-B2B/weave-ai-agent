"""요청 큐 메시지 → 기존 실행 함수. 메시지 본문은 HTTP 요청 본문과 같다.

- 색인 요청 (weave-dev-index-request): A-MQ-01 IndexRequest → run_ingest
- 분석 요청 (weave-dev-analysis-request): {"aiAnalysisId", "analysisType", "input"}
    DECOMPOSITION → run_decomposition (Agent1), SCOPE → run_scope_analysis (Agent2), IMPACT → run_impact_analysis (Agent3),
    DOC_REVIEW → run_doc_review (문서 검토 에이전트, #93)
실행 함수는 에이전트 오류를 실패 결과로 바꿔 보내므로, 여기까지 올라오는 예외는 결과 발행 실패뿐이다.
"""
from __future__ import annotations

from pydantic import ValidationError

from app.worker.runner import Job, Unprocessable


def index_job(body: dict) -> Job:
    from app.api import internal_ingest_routes as ingest
    from app.indexing.messages import IndexResult
    from app.indexing.pipeline import extraction_version_for

    try:
        req = ingest.IndexRequest.model_validate(body)
    except ValidationError as e:
        raise Unprocessable(f"색인 요청 형식 오류: {e.errors()[:3]}") from None

    def give_up() -> None:
        ingest._report(req.document_version_id, IndexResult(
            status="FAILED", request_id=req.request_id, document_version_id=req.document_version_id,
            failed_stage="EMBED", error_code="INDEXING_UNAVAILABLE", retryable=True,
            error_message="여러 번 시도했지만 색인 결과를 보내지 못했습니다.", extraction_version=extraction_version_for(req)))

    return Job(key=f"index:{req.request_id}", run=lambda: ingest.run_ingest(req), give_up=give_up)


def analysis_job(body: dict) -> Job:
    from app.api import internal_analysis_routes as analysis

    try:
        req = analysis.AnalysisRequest.model_validate(body)
    except ValidationError as e:
        raise Unprocessable(f"분석 요청 형식 오류: {e.errors()[:3]}") from None
    key = f"analysis:{req.ai_analysis_id}"
    try:
        task, inp = analysis.resolve(req)
    except analysis.InvalidAnalysisRequest as e:
        # 입력이 틀린 요청은 다시 보내도 같다 — 실패로 알리고 끝낸다 (발행 실패는 재시도)
        return Job(key=key, run=lambda: analysis._report(req.ai_analysis_id, None, ("INVALID_INPUT", False)),
                   give_up=lambda: None)

    return Job(key=key, run=lambda: task(req.ai_analysis_id, inp),
               give_up=lambda: analysis._report(req.ai_analysis_id, None, ("INTERNAL_ERROR", True)))
