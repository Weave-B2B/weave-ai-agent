"""범위 분석 결과를 백엔드 ai_analysis에 전달한다 (complete / fail).

백엔드는 complete를 받으면 결과를 검증하고 근거를 item_evidence에 저장한다(ScopeAnalysisResultHandler).
같은 결과를 다시 보내도 백엔드가 이미 완료된 분석은 건너뛴다. 오류 분류는 색인 결과 전달(app.indexing.backend)과 같다.
"""
from typing import Optional
from uuid import UUID

import httpx

from app.indexing.backend import TOKEN_HEADER


class AnalysisReportError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class AnalysisResultClient:
    def __init__(self, base_url: str, service_token: str, *, path: str, timeout: float = 10.0,
                 http: Optional[httpx.Client] = None):
        if "{ai_analysis_id}" not in path:
            raise ValueError("path에 {ai_analysis_id}가 있어야 합니다.")
        self._path = path
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {TOKEN_HEADER: service_token}

    def close(self) -> None:
        self._http.close()

    def complete(self, ai_analysis_id: UUID, output: dict) -> None:
        """output = Agent2Output JSON (스키마 그대로, snake_case)."""
        self._post(f"{self._path.format(ai_analysis_id=ai_analysis_id)}/complete", output)

    def fail(self, ai_analysis_id: UUID, error_code: str, retryable: bool) -> None:
        self._post(f"{self._path.format(ai_analysis_id=ai_analysis_id)}/fail",
                   {"errorCode": error_code, "retryable": retryable})

    def _post(self, url: str, body: dict) -> None:
        try:
            resp = self._http.post(url, json=body, headers=self._headers)
        except httpx.TransportError as e:
            raise AnalysisReportError(f"백엔드 호출 실패: {type(e).__name__}", retryable=True) from e
        if resp.status_code >= 500 or resp.status_code == 429:
            raise AnalysisReportError(f"백엔드 일시 오류 {resp.status_code}", retryable=True, status_code=resp.status_code)
        if resp.status_code >= 400:
            raise AnalysisReportError(f"백엔드가 결과를 거부함 {resp.status_code}: {resp.text[:300]}",
                                      retryable=False, status_code=resp.status_code)


def build_client(settings) -> AnalysisResultClient:
    if not settings.backend_base_url:
        raise ValueError("BACKEND_BASE_URL이 비어 있어 분석 결과를 보낼 수 없습니다.")
    return AnalysisResultClient(settings.backend_base_url, settings.backend_internal_token,
                                path=settings.backend_analysis_result_path, timeout=settings.backend_timeout_sec)
