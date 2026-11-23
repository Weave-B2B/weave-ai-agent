"""엑셀 단가표 실연동: 백엔드 단가표 변환·Run 계산 컨텍스트 리비전·단가 변경 시 승인 차단 검증."""
from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from app.kb.backend_rates import to_card
from app.orchestration.http_run_state import HttpRunStateClient
from app.orchestration.run_state import (
    CalculationContext,
    RunInputsView,
    UpdateCalculationContextRequest,
)


def _sample_agent1_input(project_id: str) -> dict:
    return {
        "context": {
            "case_id": f"CR-{uuid4().hex[:8]}",
            "project_id": project_id,
            "baseline_id": "BL-1",
            "baseline_as_of": "2026-09-01",
        },
        "received_at": "2026-10-12T09:00:00+09:00",
        "sender_role": "client",
        "message_text": "쿠폰 2장 중복 적용 기능을 추가해 주세요.",
    }


def test_backend_rate_to_card_preserves_decimal_and_metadata():
    resp = {
        "rateCardVersionId": str(uuid4()),
        "rateCardVersionNo": 2,
        "rateEffectiveDate": "2026-09-01",
        "hoursPerMd": 8,
        "rateCurrency": "KRW",
        "roleRates": [
            {"jobRoleCode": "PM", "internalRate": 520000, "sellingRate": 750000},
            {"jobRoleCode": "BE", "internalRate": 500000.5, "sellingRate": 750000},
        ],
    }
    card = to_card(resp)
    assert card["synthetic"] is False
    assert card["hours_per_md"] == "8"
    assert card["rate_card_version_no"] == 2
    assert card["rates"]["PM"]["price"] == 750000
    assert card["rates"]["BE"]["cost"] == 500000.5
    assert card["rates"]["BE"]["price"] == 750000


def test_run_inputs_view_prefers_latest_calculation_context_rate_card():
    run_id = str(uuid4())
    project_id = str(uuid4())
    v1_card = str(uuid4())
    v2_card = str(uuid4())
    doc_ver = str(uuid4())

    view = RunInputsView.model_validate({
        "run_id": run_id,
        "agent1_input": _sample_agent1_input(project_id),
        "rate_card_id": v1_card,
        "calculation_context": {
            "rate_card_id": v2_card,
            "workbook_document_version_id": doc_ver,
            "workbook_sha256": "a" * 64,
            "reason": "BE 단가 인상 반영",
        },
        "calculation_context_revision": 2,
    })
    inputs = view.to_inputs()
    assert inputs.rate_card_id == v2_card
    assert inputs.calculation_context is not None
    assert inputs.calculation_context.workbook_document_version_id == doc_ver
    assert inputs.calculation_context_revision == 2


def test_http_run_state_client_posts_calculation_context():
    run_id = str(uuid4())
    actor_id = str(uuid4())
    new_rate_id = str(uuid4())
    doc_ver_id = str(uuid4())
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "run_id": run_id,
                "operation_id": "op-ctx-1",
                "state_revision": 4,
                "status": "RUNNING",
                "result_id": None,
                "result_revision": None,
                "replayed": False,
            },
        )

    client = HttpRunStateClient(
        "http://backend.local",
        "secret-token",
        actor_id,
        http=httpx.Client(base_url="http://backend.local", transport=httpx.MockTransport(handler)),
    )
    receipt = client.update_calculation_context(
        UpdateCalculationContextRequest(
            run_id=run_id,
            expected_state_revision=3,
            operation_id="op-ctx-1",
            rate_card_id=new_rate_id,
            workbook_document_version_id=doc_ver_id,
            workbook_sha256="b" * 64,
            reason="새 단가표로 재산정",
            updated_by=actor_id,
        )
    )
    assert captured["method"] == "POST"
    assert captured["path"] == f"/internal/v1/runs/{run_id}/calculation-context"
    assert captured["body"]["rate_card_id"] == new_rate_id
    assert captured["body"]["workbook_document_version_id"] == doc_ver_id
    assert receipt.state_revision == 4
    assert receipt.status == "RUNNING"


def test_prototype_backend_bridge_guards_synthetic_ids_and_missing_auth(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from prototype.backend_bridge_routes import router

    monkeypatch.setenv("PROTOTYPE_LAB", "1")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    for key in ('prototype_backend_company_id','prototype_backend_member_id','prototype_backend_member_role'):
        monkeypatch.setattr(settings,key,'')

    # BACKEND_BASE_URL 미설정 시 로컬 SQLite나 합성 데이터로 자동 전환하지 않고 503 반환
    monkeypatch.setattr(settings, "backend_base_url", "")
    r_status = client.get("/prototype-lab/backend/status")
    assert r_status.status_code == 200
    assert r_status.json()["backend_connected"] is False

    # 합성 프로젝트 ID는 실제 UUID가 아니므로 400 차단
    monkeypatch.setattr(settings, "backend_base_url", "http://backend.local")
    r_synth = client.get(
        "/prototype-lab/backend/projects/P-COM-101/rate-cards/current",
        headers={
            "X-Dev-Company-Id": str(uuid4()),
            "X-Dev-Member-Id": str(uuid4()),
            "X-Dev-Member-Role": "ADMIN",
        },
    )
    assert r_synth.status_code == 400

    # 사용자 인증 헤더 없으면 401 차단 (내부 서비스 토큰으로 ADMIN 권한 우회 금지)
    r_no_auth = client.get(f"/prototype-lab/backend/projects/{uuid4()}/rate-cards/current")
    assert r_no_auth.status_code == 401


def test_prototype_pm_controls_forward_user_contract_and_reject_client_bundle(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from prototype import backend_bridge_routes as bridge
    monkeypatch.setenv("PROTOTYPE_LAB","1")
    calls=[]
    def proxy(request, method, path, **kwargs):
        calls.append((method,path,kwargs.get("json_body")))
        assert bridge._user_headers(request)["authorization"] == "Bearer test-user"
        return {"status":"waiting_final_review","stateRevision":4}
    monkeypatch.setattr(bridge,"_proxy",proxy)
    app=FastAPI();app.include_router(bridge.router);client=TestClient(app)
    path=f"/prototype-lab/backend/projects/{uuid4()}/change-requests/{uuid4()}/orchestrations"
    headers={"Authorization":"Bearer test-user"}
    answer={"expectedStateRevision":3,"operationId":"answer-1","questionId":"q1","answer":"가능","disposition":"provided"}
    assert client.post(path+"/answers",json=answer,headers=headers).status_code == 200
    assert calls[-1][0] == "POST" and calls[-1][1].endswith("/answers") and calls[-1][2] == answer
    review={"expectedStateRevision":4,"operationId":"review-1","decision":"approve"}
    assert client.post(path+"/final-review",json={**review,"bundle":{"forged":True}},headers=headers).status_code == 422
    assert client.post(path+"/final-review",json=review,headers=headers).status_code == 200
    assert calls[-1][2] == review
    assert client.get(path+"/status",headers=headers).status_code == 200
    assert calls[-1][0] == "GET"


def test_bridge_health_does_not_label_a_404_as_connected(monkeypatch):
    from prototype import backend_bridge_routes as bridge
    from app.core.config import settings
    monkeypatch.setattr(settings,"backend_base_url","http://backend.local")
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,path): return httpx.Response(404,json={"message":"wrong endpoint"})
    monkeypatch.setattr(bridge.httpx,"Client",Client)
    assert bridge.bridge_status()["backend_connected"] is False


def test_integrated_workspace_uses_only_explicit_local_account_without_exposing_credentials(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from prototype import backend_bridge_routes as bridge
    monkeypatch.setenv('PROTOTYPE_LAB','1')
    monkeypatch.setattr(bridge.settings,'prototype_backend_company_id',str(uuid4()))
    monkeypatch.setattr(bridge.settings,'prototype_backend_member_id',str(uuid4()))
    monkeypatch.setattr(bridge.settings,'prototype_backend_member_role','PM')
    monkeypatch.setattr(bridge.settings,'prototype_backend_actor_label','테스트 PM')
    monkeypatch.setattr(bridge,'bridge_status',lambda:{'backend_connected':True})
    app=FastAPI();app.include_router(bridge.router);client=TestClient(app)
    response=client.get('/prototype-lab/backend/context')
    assert response.json()=={'backend_connected':True,'account_configured':True,'actor_label':'테스트 PM'}
    calls=[]
    def proxy(request,method,path,**kw):
        calls.append(bridge._user_headers(request))
        return {'content':[]}
    monkeypatch.setattr(bridge,'_proxy',proxy)
    assert client.get('/prototype-lab/backend/projects').status_code==200
    assert calls[-1]['x-dev-member-role']=='PM'
    # Explicit browser identity wins; partial identity never acquires server ADMIN/PM.
    client.get('/prototype-lab/backend/projects',headers={'Authorization':'Bearer user'})
    assert calls[-1]=={'authorization':'Bearer user'}
    assert client.get('/prototype-lab/backend/projects',headers={'X-Dev-Member-Role':'ADMIN'}).status_code==401
    assert client.get('/prototype-lab/backend/context',headers={'Origin':'https://other.example'}).status_code==403
    assert client.get('/prototype-lab/backend/context',headers={'Origin':'null'}).status_code==403
    assert client.get('/prototype-lab/backend/workbooks/example').headers['content-type'].startswith(bridge.XLSX_MIME)
    assert client.get('/prototype-lab/backend/workbooks/other').status_code==422
