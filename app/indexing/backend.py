"""색인 결과(A-MQ-02)를 백엔드에 전달한다. SQS 연결 전에는 HTTP로 바로 보내고, SQS 수신부가 생겨도 같은 결과 형식을 쓴다.

백엔드는 받은 결과를 DocumentProcessingService.applyIngestResult로 문서 처리 상태에 반영한다.
경로는 설정(BACKEND_INGEST_RESULT_PATH)으로 바꿀 수 있다 — 로컬에서 정식 내부 API가 없으면 개발용 /dev 경로를 쓴다.
"""
from typing import Optional
from uuid import UUID

import httpx

from app.indexing.messages import IndexResult

TOKEN_HEADER = "X-Internal-Token"
DEFAULT_PATH = "/internal/v1/document-versions/{document_version_id}/ingest-result"


class IngestReportError(RuntimeError):
    """결과 전달 실패. retryable이면 같은 결과를 그대로 다시 보내도 된다 (백엔드는 requestId로 최신 요청만 반영)."""

    def __init__(self, message: str, *, retryable: bool, status_code: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class IngestResultClient:
    def __init__(self, base_url: str, service_token: str, *, path: str = DEFAULT_PATH,
                 timeout: float = 10.0, http: Optional[httpx.Client] = None):
        if "{document_version_id}" not in path:
            raise ValueError("path에 {document_version_id}가 있어야 합니다.")
        self._path = path
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {TOKEN_HEADER: service_token}

    def close(self) -> None:
        self._http.close()

    def report(self, document_version_id: UUID, result: IndexResult) -> dict:
        """결과를 보내고 백엔드의 처리 상태 응답을 돌려준다."""
        url = self._path.format(document_version_id=document_version_id)
        try:
            resp = self._http.post(url, json=result.to_json(), headers=self._headers)
        except httpx.TransportError as e:
            raise IngestReportError(f"백엔드 호출 실패: {type(e).__name__}", retryable=True) from e
        if resp.status_code >= 500 or resp.status_code == 429:
            raise IngestReportError(f"백엔드 일시 오류 {resp.status_code}", retryable=True, status_code=resp.status_code)
        if resp.status_code >= 400:
            raise IngestReportError(f"백엔드가 결과를 거부함 {resp.status_code}: {resp.text[:300]}",
                                    retryable=False, status_code=resp.status_code)
        return resp.json() if resp.content else {}


def build_client(settings) -> IngestResultClient:
    if not settings.backend_base_url:
        raise ValueError("BACKEND_BASE_URL이 비어 있어 색인 결과를 보낼 수 없습니다.")
    return IngestResultClient(settings.backend_base_url, settings.backend_internal_token,
                              path=settings.backend_ingest_result_path, timeout=settings.backend_timeout_sec)
