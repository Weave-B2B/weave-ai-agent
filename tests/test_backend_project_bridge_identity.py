"""Developer prototype keeps request identity separate from project-local CR numbers."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.orchestration import backend_project_bridge as bridge
from prototype import backend_bridge_routes as routes


def test_same_display_number_in_two_projects_does_not_select_a_shared_run():
    a, b = str(uuid4()), str(uuid4())
    assert bridge.select_project_run_id(a, "CR-01", {a: {}, "CR-01": {}}) == a
    assert bridge.select_project_run_id(b, "CR-01", {a: {}, "CR-01": {}}) is None
    assert bridge.select_project_run_id(b, "CR-01", {b: {}}) == b


def test_legacy_uuid_run_is_preserved_only_from_project_filtered_runs():
    cr_id = str(uuid4())
    old = "CR-" + str(uuid4())
    assert bridge.select_project_run_id(cr_id, old, {old: {}}) == old
    assert bridge.select_project_run_id(cr_id, old, {}) is None
    assert bridge.select_project_run_id(cr_id, old, {old: {}, cr_id: {}}) == cr_id


def test_display_number_cannot_resolve_database_request(monkeypatch):
    import psycopg

    monkeypatch.setattr(bridge, "is_backend_project_id", lambda _: True)
    def forbidden(*args, **kwargs):
        pytest.fail("display-only number must not access the database")
    monkeypatch.setattr(psycopg, "connect", forbidden)
    assert bridge.sync_change_request_to_backend(str(uuid4()), "CR-01", "title", "text") is None


def test_creation_proxy_uses_backend_assigned_identifiers(monkeypatch):
    project_id, request_id = str(uuid4()), str(uuid4())
    monkeypatch.setenv("PROTOTYPE_LAB", "1")
    calls = []
    def proxy(request, method, path, **kwargs):
        calls.append((method, path, kwargs["json_body"]))
        assert routes._user_headers(request)["authorization"] == "Bearer test-user"
        return {"changeRequestId": request_id, "crNumber": "CR-01"}
    monkeypatch.setattr(routes, "_proxy", proxy)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    body = {"title": "요청", "originalText": "원문", "channel": "OTHER", "priority": "NORMAL",
            "receivedAt": "2026-11-27T00:00:00Z", "fileObjectIds": []}
    response = client.post(f"/prototype-lab/backend/projects/{project_id}/change-requests",
                           json=body, headers={"Authorization": "Bearer test-user"})
    assert response.status_code == 200
    assert response.json() == {"changeRequestId": request_id, "crNumber": "CR-01"}
    assert calls == [("POST", f"/api/v1/projects/{project_id}/change-requests", body)]
