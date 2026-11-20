"""백엔드 공식 계산 API 클라이언트 (POST /internal/v1/estimates/calculations).

백엔드가 단가표·정책을 읽어 금액·일정을 계산하고 검증한다.
호출 실패 시 로컬 계산이나 예시 응답으로 대체하지 않는다.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.indexing.backend import TOKEN_HEADER
from app.tools.errors import ToolInputError


class BackendCalcError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class CalcClient:
    def __init__(self, base_url: str, service_token: str, *,
                 path: str = "/internal/v1/estimates/calculations", timeout: float = 10.0,
                 http: Optional[httpx.Client] = None):
        self._path = path
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {TOKEN_HEADER: service_token}

    def policy_hash(self) -> str:
        try:
            response = self._http.get(self._path.rsplit('/', 1)[0] + '/policy', headers=self._headers)
            response.raise_for_status()
            return response.json()['policy_hash']
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise BackendCalcError('계산 정책을 확인하지 못했습니다.') from e

    def calculate(self, body: dict) -> dict:
        data = self._post(self._path, body)
        if data.get("status") not in ("OK", "NEEDS_INPUT"):
            raise BackendCalcError("백엔드 계산 응답 형식이 올바르지 않습니다.")
        if data["status"] == "OK" and (not isinstance(data.get("pricing"), dict)
                or not isinstance(data.get("snapshot"), dict) or not isinstance(data.get("schedule"), dict)):
            raise BackendCalcError("백엔드 계산 응답에 금액·일정·스냅샷이 없습니다.")
        return data

    def labor_totals(self, body: dict) -> dict:
        data = self._post(self._path.rsplit('/', 1)[0] + '/labor-totals', body)
        if data.get("status") == "NEEDS_INPUT":
            return {"status": "NEEDS_INPUT", "missing": data.get("missing", []), "totals": None}
        if not {"md", "md_by_role", "price", "cost", "margin", "rate_card_id"} <= data.keys():
            raise BackendCalcError("백엔드 인건비 응답 형식이 올바르지 않습니다.")
        return data

    def validate(self, body: dict) -> dict:
        data = self._post(self._path.rsplit('/', 1)[0] + '/validations', body)
        if data.get("valid") is not True or data.get("snapshot_id") != body["pricing"].get("snapshot_id"):
            raise BackendCalcError("백엔드 계산 검증 응답이 일치하지 않습니다.")
        return data

    def _post(self, path: str, body: dict) -> dict:
        try:
            resp = self._http.post(path, json={k: v for k, v in body.items() if v is not None},
                                   headers=self._headers)
        except httpx.TransportError as e:
            raise BackendCalcError(f"백엔드 계산 API 호출 실패: {type(e).__name__}") from e
        if resp.status_code in (400, 422):
            raise ToolInputError(f"백엔드 계산 입력 오류: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise BackendCalcError(f"백엔드 계산 API 오류 {resp.status_code}: {resp.text[:300]}",
                                   status_code=resp.status_code)
        try:
            data = resp.json()
        except ValueError as e:
            raise BackendCalcError("백엔드 계산 응답이 JSON이 아닙니다.") from e
        if not isinstance(data, dict):
            raise BackendCalcError("백엔드 계산 응답 형식이 올바르지 않습니다.")
        return data


def build_calc_client(settings) -> CalcClient:
    if not settings.backend_base_url or not settings.backend_internal_token:
        raise BackendCalcError("계산 백엔드 연동에는 BACKEND_BASE_URL이 필요합니다.")
    return CalcClient(settings.backend_base_url, settings.backend_internal_token,
                      path=settings.backend_calculation_path, timeout=settings.backend_timeout_sec)


def calculation_client(rate_card_id: str):
    from app.core.config import settings
    from app.kb.backend_rates import is_backend_rate_id
    # 실제 UUID는 fixture 설정이 있어도 무조건 공식 Backend로 보낸다.
    if is_backend_rate_id(rate_card_id):
        return build_calc_client(settings)
    if settings.calculation_mode == "fixture":
        from app.tools.fixture_calculation import FixtureCalcClient
        return FixtureCalcClient(rate_card_id)
    raise ToolInputError("공식 계산에는 백엔드 프로젝트·단가표 UUID가 필요합니다. 합성 시연은 CALCULATION_MODE=fixture로 실행해주세요.")
