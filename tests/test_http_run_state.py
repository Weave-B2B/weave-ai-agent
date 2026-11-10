import json

import httpx
import pytest

from app.orchestration.http_run_state import (
    ACTOR_HEADER, TOKEN_HEADER, HttpRunStateClient, RunAccessDenied, RunRequestRejected,
)
from app.orchestration.run_state import (
    CommitResultRequest, InvalidStateTransition, OperationConflict, RecordEditRequest, ResolveReviewRequest,
    ResultNotFound, RunNotFound, RunStateClient, RunStateError, StartAgentRequest, StatePersistenceError,
    StateRevisionConflict,
)

AGENTS = {a: {"status": "NOT_STARTED", "current_result_id": None, "result_revision": None,
              "upstream_result_ids": [], "stale": False} for a in ("agent1", "agent2", "agent3")}
SNAPSHOT = {"run_id": "run-1", "state_revision": 0, "status": "QUEUED", "agents": AGENTS, "pending_review": None}
RECEIPT = {"run_id": "run-1", "operation_id": "op-1", "state_revision": 1, "status": "RUNNING",
           "result_id": None, "result_revision": None, "replayed": False}


def client_with(handler):
    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(handler))
    return HttpRunStateClient("http://backend", "tok", "actor-1", http=http)


def error_client(status, body=None, text=None):
    def handler(request):
        return httpx.Response(status, json=body) if text is None else httpx.Response(status, text=text)
    return client_with(handler)


def test_satisfies_port():
    c: RunStateClient = client_with(lambda r: httpx.Response(200, json=SNAPSHOT))
    assert c.get_state("run-1").agents["agent1"].status == "NOT_STARTED"


def test_get_state_sends_service_headers_and_parses_snapshot():
    seen = {}

    def handler(request):
        seen.update(method=request.method, path=request.url.path, headers=request.headers)
        return httpx.Response(200, json={**SNAPSHOT, "pending_review": {
            "agent_type": "agent1", "target_result_id": None, "reason": "x", "kind": "INPUT"}})
    s = client_with(handler).get_state("run-1")
    assert (seen["method"], seen["path"]) == ("GET", "/internal/v1/runs/run-1")
    assert seen["headers"][TOKEN_HEADER] == "tok" and seen["headers"][ACTOR_HEADER] == "actor-1"
    assert s.pending_review.kind == "INPUT"


@pytest.mark.parametrize("call,path", [
    (lambda c: c.start_agent(StartAgentRequest(run_id="run-1", agent_type="agent1", expected_state_revision=0,
                                               operation_id="op-1")), "agents/start"),
    (lambda c: c.commit_result(CommitResultRequest(run_id="run-1", expected_state_revision=1, operation_id="op-1",
                                                   agent_type="agent1", result_id="r1", result_payload={"k": 1})),
     "results"),
    (lambda c: c.record_edit(RecordEditRequest(run_id="run-1", expected_state_revision=1, operation_id="op-1",
                                               agent_type="agent1", target_result_id="r1", new_result_id="r2",
                                               edited_by="actor-1", edited_payload={"k": 2})), "edits"),
    (lambda c: c.resolve_review(ResolveReviewRequest(run_id="run-1", expected_state_revision=1, operation_id="op-1",
                                                     resolved_by="actor-1", decision="APPROVED")),
     "review-resolutions"),
])
def test_writes_post_body_without_run_id(call, path):
    seen = {}

    def handler(request):
        seen.update(method=request.method, path=request.url.path, body=json.loads(request.content))
        return httpx.Response(200, json=RECEIPT)
    receipt = call(client_with(handler))
    assert (seen["method"], seen["path"]) == ("POST", f"/internal/v1/runs/run-1/{path}")
    assert "run_id" not in seen["body"] and seen["body"]["operation_id"] == "op-1"
    assert receipt.state_revision == 1 and receipt.replayed is False


def test_get_result_parses_committed_result():
    body = {"run_id": "run-1", "agent_type": "agent1", "result_id": "r1", "result_revision": 1,
            "execution_status": "COMPLETED", "upstream_result_ids": [], "result_payload": {"k": 1},
            "created_at": "2026-01-01T00:00:00Z", "is_current": True}
    r = client_with(lambda req: httpx.Response(200, json=body)).get_result("run-1", "r1")
    assert r.is_current and r.result_payload == {"k": 1}


def test_create_run_posts_project_and_change_request():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, body=json.loads(request.content))
        return httpx.Response(200, json=SNAPSHOT)
    client_with(handler).create_run("p-1", "cr-1")
    assert seen == {"path": "/internal/v1/runs", "body": {"project_id": "p-1", "change_request_id": "cr-1"}}


@pytest.mark.parametrize("status,code,exc,retryable", [
    (404, "RUN_NOT_FOUND", RunNotFound, False),
    (404, "RUN_RESULT_NOT_FOUND", ResultNotFound, False),
    (409, "RUN_OPERATION_CONFLICT", OperationConflict, False),
    (409, "RUN_INVALID_STATE_TRANSITION", InvalidStateTransition, False),
    (409, "RUN_RESULT_ID_CONFLICT", InvalidStateTransition, False),
    (401, "GLOBAL_UNAUTHORIZED", RunAccessDenied, False),
    (403, "GLOBAL_FORBIDDEN", RunAccessDenied, False),
    (400, "GLOBAL_INVALID_INPUT", RunRequestRejected, False),
    (413, "RUN_PAYLOAD_TOO_LARGE", RunRequestRejected, False),
    (500, "GLOBAL_INTERNAL_ERROR", StatePersistenceError, True),
    (503, "", StatePersistenceError, True),
])
def test_error_mapping(status, code, exc, retryable):
    with pytest.raises(exc) as e:
        error_client(status, {"code": code, "message": "m", "retryable": retryable}).get_state("run-1")
    assert type(e.value) is exc and e.value.retryable is retryable and e.value.run_id == "run-1"


def test_revision_conflict_carries_expected_and_actual():
    body = {"code": "RUN_STATE_REVISION_CONFLICT", "message": "m", "retryable": False,
            "expected_state_revision": 0, "actual_state_revision": 3}
    with pytest.raises(StateRevisionConflict) as e:
        error_client(409, body).start_agent(StartAgentRequest(
            run_id="run-1", agent_type="agent1", expected_state_revision=0, operation_id="op-1"))
    assert (e.value.expected_revision, e.value.actual_revision, e.value.retryable) == (0, 3, False)


def test_non_json_error_body_and_unknown_code_are_handled():
    with pytest.raises(StatePersistenceError):
        error_client(502, text="<html>bad gateway</html>").get_state("run-1")
    with pytest.raises(RunStateError) as e:
        error_client(409, {"code": "GLOBAL_CONFLICT", "message": "m"}).get_state("run-1")
    assert type(e.value) is RunStateError and not e.value.retryable


def test_transport_error_is_retryable_persistence_error():
    def handler(request):
        raise httpx.ConnectTimeout("timeout")
    with pytest.raises(StatePersistenceError) as e:
        client_with(handler).get_state("run-1")
    assert e.value.retryable
