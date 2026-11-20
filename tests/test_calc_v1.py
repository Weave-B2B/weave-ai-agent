"""AI 계산 도구는 공식을 실행하지 않고 Backend 계약과 고정 응답을 사용한다."""
import copy
import json

import httpx
import pytest

from app.tools.backend_calc import BackendCalcError, CalcClient, calculation_client
from app.tools.calc_cost import calc_cost
from app.tools.calc_pricing import calc_pricing
from app.tools.errors import ToolInputError


def client(handler):
    return CalcClient("http://backend", "token", http=httpx.Client(
        base_url="http://backend", transport=httpx.MockTransport(handler)))


def response():
    return {"status": "OK", "missing": [], "pricing": {"supply_amount": 1400000},
            "snapshot": {"snapshot_id": "snapshot"}, "schedule": {"estimated_end_date": None}}


def test_posts_to_official_calculation_endpoint():
    seen = {}
    def handler(request):
        seen.update(path=request.url.path, token=request.headers.get("X-Internal-Token"), body=json.loads(request.content))
        return httpx.Response(200, json=response())
    result = client(handler).calculate({"rate_card_id": "r", "project_id": "p",
        "lines": [{"key": "L1", "role": "BE", "md": 2}], "adjustments": None})
    assert result["pricing"]["supply_amount"] == 1400000
    assert seen == {"path": "/internal/v1/estimates/calculations", "token": "token",
                    "body": {"rate_card_id": "r", "project_id": "p", "lines": [{"key": "L1", "role": "BE", "md": 2}]}}


@pytest.mark.parametrize("status,exception", [(400, ToolInputError), (422, ToolInputError),
                                               (401, BackendCalcError), (503, BackendCalcError)])
def test_failures_never_fall_back(status, exception):
    with pytest.raises(exception):
        client(lambda _: httpx.Response(status, json={"code": "error"})).calculate({"lines": []})


@pytest.mark.parametrize("body", [[], {}, {"status": "OK"}, {"status": "OK", "pricing": {}, "snapshot": {}}])
def test_incomplete_calculation_response_is_technical_failure(body):
    with pytest.raises(BackendCalcError):
        client(lambda _: httpx.Response(200, json=body)).calculate({"lines": []})


def test_invalid_json_and_timeout_are_technical_failures():
    with pytest.raises(BackendCalcError):
        client(lambda _: httpx.Response(200, text="not-json")).calculate({})
    def timeout(request): raise httpx.ReadTimeout("timeout", request=request)
    with pytest.raises(BackendCalcError): client(timeout).calculate({})


def test_labor_totals_and_validation_use_backend_routes():
    seen = []
    totals = {"md": 2, "md_by_role": {"BE": 2}, "price": 1400000, "cost": 1000000,
              "margin": 0.2857, "rate_card_id": "r", "currency": "KRW", "vat_included": False}
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=totals if request.url.path.endswith("labor-totals")
                              else {"valid": True, "snapshot_id": "s"})
    c = client(handler)
    assert c.labor_totals({"lines": []}) == totals
    assert c.validate({"pricing": {"snapshot_id": "s"}})["valid"]
    assert seen == ["/internal/v1/estimates/labor-totals", "/internal/v1/estimates/validations"]


def test_bad_validation_identity_is_not_accepted():
    with pytest.raises(BackendCalcError):
        client(lambda _: httpx.Response(200, json={"valid": True, "snapshot_id": "other"})).validate(
            {"pricing": {"snapshot_id": "s"}})


def test_actual_uuid_always_uses_backend_even_in_fixture_mode(monkeypatch):
    from app.tools import backend_calc
    calls = []
    class Fake:
        def calculate(self, body):
            calls.append(body)
            return response()
    monkeypatch.setattr(backend_calc, "build_calc_client", lambda _: Fake())
    rate_id = "11111111-1111-1111-1111-111111111111"
    assert calc_pricing([{"role": "BE", "md": 2}], rate_id, project_id="p")["pricing"]["snapshot_id"] == "snapshot"
    assert calls[0]["project_id"] == "p" and calls[0]["rate_card_id"] == rate_id


def test_backend_mode_does_not_use_synthetic_rates(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "calculation_mode", "backend")
    with pytest.raises(ToolInputError, match="UUID"):
        calc_cost([{"role": "BE", "md": 1}], "RC-SYNTH-2026H2-v1")


def test_missing_backend_settings_do_not_select_fixture(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "backend_base_url", "")
    with pytest.raises(BackendCalcError):
        calculation_client("11111111-1111-1111-1111-111111111111")


def test_backend_failure_is_http_502_instead_of_input_error():
    from app.api.agent3_routes import _guard
    from fastapi import HTTPException
    def failed(): raise BackendCalcError("private backend diagnostics")
    with pytest.raises(HTTPException) as error:
        _guard(failed)
    assert error.value.status_code == 502
    assert "private" not in error.value.detail


def test_fixture_replays_known_input_and_rejects_unknown_input(monkeypatch):
    from app.tools import fixture_calculation as module
    from app.tools.calculation_snapshot import validate_pricing
    rate_id = "RC-SYNTH-2026H2-v1"
    p = calc_pricing([{"key": "a", "role": "FE", "md": 1}], rate_id, project_id="p")["pricing"]
    assert p["calculated_by"] == "fixture-replay"
    validate_pricing(p, rate_id)
    bad = copy.deepcopy(p); bad["total_with_vat"] += 1
    with pytest.raises(ToolInputError): validate_pricing(bad, rate_id)
    # Explicitly empty replay catalogue: no recorder or calculator may manufacture a result.
    monkeypatch.setattr(module, "_fixtures", lambda: {})
    # Bypass the optional recording hook to check the production replay implementation.
    from app.tools.fixture_calculation import replay_response
    with pytest.raises(BackendCalcError, match="고정 예시"):
        replay_response({"lines": []}, {}, {})
