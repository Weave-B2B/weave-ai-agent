"""KB_SOURCE=backend: 백엔드 단가표를 지식베이스로 읽어 Agent3 산정 도구(calc_cost)가 그대로 쓰는지 검증."""
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.kb import backend_rates, loader
from app.kb.backend_rates import RateCardClient, to_card
from app.tools.calc_cost import calc_cost

RATE_CARD_ID = str(uuid4())
PROJECT_ID = str(uuid4())

RATE_CARD_RESPONSE = {
    "rateCardVersionId": RATE_CARD_ID,
    "rateCardVersionNo": 1,
    "rateEffectiveDate": "2026-01-01",
    "hoursPerMd": 8,
    "rateCurrency": "KRW",
    "roleRates": [
        {
            "roleRateId": str(uuid4()),
            "jobRoleId": str(uuid4()),
            "jobRoleCode": "BE",
            "jobRoleName": "백엔드 개발자",
            "internalRate": 500000,
            "sellingRate": 700000
        },
        {
            "roleRateId": str(uuid4()),
            "jobRoleId": str(uuid4()),
            "jobRoleCode": "FE",
            "jobRoleName": "프론트엔드 개발자",
            "internalRate": 450000,
            "sellingRate": 650000
        }
    ]
}


@pytest.fixture
def rate_backend(monkeypatch):
    calls = []

    def handle(req: httpx.Request):
        calls.append(req)
        path = req.url.path
        if RATE_CARD_ID in path:
            return httpx.Response(200, json=RATE_CARD_RESPONSE)
        if PROJECT_ID in path:
            return httpx.Response(200, json=RATE_CARD_RESPONSE)
        return httpx.Response(400, json={"code": "PROJECT_RATE_CARD_NOT_FOUND"})

    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(settings, "backend_base_url", "http://backend")
    monkeypatch.setattr(settings, "backend_internal_token", "test-token")
    monkeypatch.setattr(backend_rates, "build_rate_client",
                        lambda s: RateCardClient("http://backend", "test-token", http=http))
    loader.clear_remote()
    yield calls
    loader.clear_remote()


def test_rate_card_client_fetch_and_convert(rate_backend):
    client = RateCardClient("http://backend", "test-token", http=rate_backend[0] if rate_backend else None)
    card = to_card(RATE_CARD_RESPONSE)
    assert card["unit"] == "KRW/MD"
    assert card["currency"] == "KRW"
    assert card["rates"]["BE"]["cost"] == 500000
    assert card["rates"]["BE"]["price"] == 700000
    assert card["rates"]["FE"]["cost"] == 450000
    assert card["rates"]["FE"]["price"] == 650000


def test_loader_fetches_remote_rate_card_on_demand(rate_backend):
    cards = loader.rate_cards()
    # 처음에는 백엔드 단가표가 없으나, 조회 시도 시 백엔드에서 읽어온다
    card = cards.get(RATE_CARD_ID)
    assert card is not None
    assert card["rates"]["BE"]["price"] == 700000


def test_calc_cost_tool_uses_backend_rate_card(rate_backend):
    lines = [
        {"task": "PG 결제 연동", "role": "BE", "md": 5.0},
        {"task": "결제 화면 개발", "role": "FE", "md": 2.0}
    ]
    # BE 5.0 MD * 70만 = 350만, FE 2.0 MD * 65만 = 130만 -> 총 판매가 480만
    # BE 5.0 MD * 50만 = 250만, FE 2.0 MD * 45만 = 90만 -> 총 원가 340만
    totals = calc_cost(lines, RATE_CARD_ID)
    assert totals["md"] == 7.0
    assert totals["cost"] == 3400000
    assert totals["price"] == 4800000
    assert totals["currency"] == "KRW"


def test_proposal_draft_endpoint():
    from starlette.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    req = {
        "case_id": "CR-2026-001",
        "project_id": PROJECT_ID,
        "review_version_id": str(uuid4()),
        "request_items": ["결제 모듈 연동 추가"],
        "tasks": ["PG사 연동 백엔드 API 개발"],
        "total_price": 3500000,
        "currency": "KRW"
    }
    resp = client.post("/agent3/proposal-draft", json=req)
    assert resp.status_code == 200
    data = resp.json()
    assert "[CR-2026-001] 변경 요청 검토 및 협의 제안서" in data["subject"]
    assert "3,500,000 KRW" in data["body"]
    assert "결제 모듈 연동 추가" in data["body"]

