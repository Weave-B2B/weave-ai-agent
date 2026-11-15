"""백엔드 → AI 문서 색인 요청 (A-MQ-01). 문서 버전 등록·재시도가 커밋된 뒤 백엔드가 호출한다.

요청을 확인하면 바로 202로 답하고, 색인은 백그라운드에서 실행한다 (추출·임베딩은 수십 초 이상 걸릴 수 있다).
끝나면 결과(A-MQ-02)를 백엔드 /internal/v1/document-versions/{id}/ingest-result로 보낸다. 실패도 FAILED 결과로 보낸다.
SQS가 생기면 이 입구 대신 큐 수신부가 같은 run_ingest를 부른다.

인증: 범위 분석 입구와 같은 내부 토큰(X-Internal-Token = BACKEND_INTERNAL_TOKEN).
"""
import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.api.internal_analysis_routes import REPORT_BACKOFF_SEC, REPORT_RETRIES, _require_token
from app.core.config import settings
from app.indexing.backend import IngestReportError, build_client
from app.indexing.messages import IndexRequest, IndexResult
from app.indexing.pipeline import EXTRACTION_VERSION

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/v1", tags=["Internal (Backend → AI)"])


class IngestAccepted(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    request_id: UUID
    document_version_id: UUID
    status: str = "ACCEPTED"


@router.post("/document-ingests", status_code=202, response_model=IngestAccepted, response_model_by_alias=True)
def request_ingest(req: IndexRequest, background: BackgroundTasks,
                   x_internal_token: Optional[str] = Header(None)) -> IngestAccepted:
    _require_token(x_internal_token)
    background.add_task(run_ingest, req)
    return IngestAccepted(request_id=req.request_id, document_version_id=req.document_version_id)


def run_ingest(req: IndexRequest) -> None:
    """색인 1건을 실행하고 결과를 백엔드에 보낸다. 예외를 밖으로 던지지 않는다 (백그라운드 작업)."""
    from app.embed import build_embedder
    from app.indexing.objects import S3Store
    from app.indexing.pipeline import index_document
    from app.kb.vector_store import open_pg_store

    try:
        embedder = build_embedder(settings)
        if embedder is None:
            raise RuntimeError("EMBED_PROVIDER=none — 임베딩 모델이 없음")
        store = open_pg_store(settings, embedder)
        objects = S3Store(settings.s3_endpoint or None, settings.s3_region,
                          settings.s3_access_key_id or None, settings.s3_secret_access_key or None)
        result = index_document(req, objects, embedder, store, settings.s3_source_bucket, settings.s3_result_bucket)
    except Exception as exc:                # 설정·연결 문제는 파이프라인 밖에서 난다. 처리 중으로 멈춰 있지 않게 FAILED로 알린다
        log.exception("색인 준비 실패 document_version_id=%s", req.document_version_id)
        result = IndexResult(status="FAILED", request_id=req.request_id, document_version_id=req.document_version_id,
                             failed_stage="EMBED", error_code="INDEXING_UNAVAILABLE", retryable=True,
                             error_message=f"{type(exc).__name__}: {exc}"[:500], extraction_version=EXTRACTION_VERSION)
    _report(req.document_version_id, result)


def _report(document_version_id: UUID, result: IndexResult) -> None:
    try:
        client = build_client(settings)
    except ValueError:
        log.error("BACKEND_BASE_URL 없음 — 색인 결과를 보내지 못함 document_version_id=%s", document_version_id)
        return
    try:
        for attempt in range(1, REPORT_RETRIES + 1):
            try:
                client.report(document_version_id, result)
                return
            except IngestReportError as e:
                if not e.retryable or attempt == REPORT_RETRIES:
                    log.error("색인 결과 전달 실패 document_version_id=%s status=%s: %s", document_version_id, e.status_code, e)
                    return
                time.sleep(REPORT_BACKOFF_SEC * attempt)
    finally:
        client.close()
