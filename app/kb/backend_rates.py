"""백엔드 단가표(Rate Card)를 지식베이스로 읽는다 (KB_SOURCE=backend 또는 백엔드 UUID 단가표 ID).

백엔드 내부 API(GET /internal/v1/rate-cards/{rateCardId} 또는 /internal/v1/projects/{projectId}/rate-card)에서
RateCardResponse를 받아 loader.rate_cards() 규격으로 변환한다.
"""
from __future__ import annotations

import uuid
from typing import Optional

import httpx

from app.indexing.backend import TOKEN_HEADER

ROLES = {"PM", "UX", "FE", "BE", "QA"}


class BackendRateError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def is_backend_rate_id(rate_card_id: str) -> bool:
    try:
        uuid.UUID(rate_card_id)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class RateCardClient:
    def __init__(self, base_url: str, service_token: str, *,
                 card_path: str = "/internal/v1/rate-cards/{rate_card_id}",
                 project_path: str = "/internal/v1/projects/{project_id}/rate-card",
                 timeout: float = 10.0,
                 http: Optional[httpx.Client] = None):
        self._card_path = card_path
        self._project_path = project_path
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {TOKEN_HEADER: service_token}

    def fetch_by_id(self, rate_card_id: str) -> Optional[dict]:
        """단가표 ID로 조회. 없으면 None."""
        try:
            resp = self._http.get(self._card_path.format(rate_card_id=rate_card_id), headers=self._headers)
        except httpx.TransportError as e:
            raise BackendRateError(f"백엔드 단가표 조회 실패: {type(e).__name__}") from e
        if resp.status_code == 400 and "RATE_CARD_NOT_FOUND" in resp.text:
            return None
        if resp.status_code >= 400:
            raise BackendRateError(f"백엔드 단가표 조회 오류 {resp.status_code}: {resp.text[:300]}",
                                  status_code=resp.status_code)
        return resp.json()

    def fetch_by_project(self, project_id: str) -> Optional[dict]:
        """프로젝트 ID로 현재 단가표 조회. 없으면 None."""
        try:
            resp = self._http.get(self._project_path.format(project_id=project_id), headers=self._headers)
        except httpx.TransportError as e:
            raise BackendRateError(f"백엔드 프로젝트 단가표 조회 실패: {type(e).__name__}") from e
        if resp.status_code == 400 and "RATE_CARD_NOT_FOUND" in resp.text:
            return None
        if resp.status_code >= 400:
            raise BackendRateError(f"백엔드 프로젝트 단가표 조회 오류 {resp.status_code}: {resp.text[:300]}",
                                  status_code=resp.status_code)
        return resp.json()


def build_rate_client(settings) -> RateCardClient:
    if not settings.backend_base_url:
        raise BackendRateError("단가표 백엔드 연동에는 BACKEND_BASE_URL이 필요합니다.")
    return RateCardClient(
        settings.backend_base_url,
        settings.backend_internal_token,
        card_path=settings.backend_rate_card_path,
        project_path=settings.backend_project_rate_card_path,
        timeout=settings.backend_timeout_sec
    )


def to_card(resp: dict) -> dict:
    """백엔드 RateCardResponse → loader.rate_cards() 딕셔너리 포맷."""
    rates = {}
    for r in resp.get("roleRates", []):
        role_code = r.get("jobRoleCode")
        if role_code in ROLES:
            rates[role_code] = {
                "cost": int(r["internalRate"]) if r.get("internalRate") is not None else None,
                "price": int(r["sellingRate"]) if r.get("sellingRate") is not None else None,
                "unit": "KRW/MD",
                "currency": resp.get("rateCurrency", "KRW"),
                "vat_included": False
            }
    return {
        "unit": "KRW/MD",
        "currency": resp.get("rateCurrency", "KRW"),
        "vat_included": False,
        "synthetic": False,
        "rates": rates
    }
