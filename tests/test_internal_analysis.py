"""백엔드 → AI 범위 분석 요청 입구와 결과 전달 (POST /internal/v1/analyses)."""
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from schema.schemas import AgentError

from app.agents.base import AgentFailure
from app.api import agent2_routes, internal_analysis_routes
from app.core.config import settings
from app.main import app
from app.services.analysis_result_client import AnalysisReportError, AnalysisResultClient
from test_agent2 import INPUTS

CASE = "CR-A-001"
TOKEN = "tok"


class Recorder:
    def __init__(self, error: Exception | None = None):
        self.calls, self.error = [], error

    def complete(self, ai_analysis_id, output):
        self._call("complete", ai_analysis_id, output)

    def fail(self, ai_analysis_id, code, retryable):
        self._call("fail", ai_analysis_id, (code, retryable))

    def _call(self, kind, ai_analysis_id, payload):
        self.calls.append((kind, ai_analysis_id, payload))
        if self.error:
            raise self.error

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(settings, "backend_internal_token", TOKEN)
    monkeypatch.setattr(settings, "agent_mode", "fixture")
    monkeypatch.setattr(settings, "agent2_mode", "fixture")
    monkeypatch.setattr(internal_analysis_routes, "build_client", lambda _s: rec)
    monkeypatch.setattr(internal_analysis_routes, "REPORT_BACKOFF_SEC", 0)
    cached = agent2_routes.get_agent2                    # 테스트가 monkeypatch로 바꿔도 원본 캐시를 비운다
    cached.cache_clear()
    yield TestClient(app), rec
    cached.cache_clear()


def _body(case_input=None):
    return {"aiAnalysisId": str(uuid4()), "analysisType": "SCOPE", "input": case_input or INPUTS[CASE]}


def test_token_required(env):
    client, rec = env
    assert client.post("/internal/v1/analyses", json=_body()).status_code == 401
    assert client.post("/internal/v1/analyses", json=_body(), headers={"X-Internal-Token": "x"}).status_code == 401
    assert rec.calls == []


def test_empty_server_token_rejects_everything(env, monkeypatch):
    client, rec = env
    monkeypatch.setattr(settings, "backend_internal_token", "")
    assert client.post("/internal/v1/analyses", json=_body(), headers={"X-Internal-Token": ""}).status_code == 401


def test_accepts_runs_agent2_and_reports_complete(env):
    client, rec = env
    body = _body()
    r = client.post("/internal/v1/analyses", json=body, headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 202 and r.json() == {"aiAnalysisId": body["aiAnalysisId"], "status": "ACCEPTED"}
    (kind, ai_id, output), = rec.calls                       # 백그라운드 작업은 응답 뒤 실행된다
    assert kind == "complete" and str(ai_id) == body["aiAnalysisId"]
    assert output["meta"]["schema_version"].startswith("0.3.") and output["meta"]["result_id"]
    assert {a["item_id"] for a in output["assessments"]} == {it["item_id"] for it in INPUTS[CASE]["items"]}


def test_invalid_input_is_422_and_not_run(env):
    client, rec = env
    bad = {**_body(), "analysisType": "DECOMPOSITION"}
    assert client.post("/internal/v1/analyses", json=bad, headers={"X-Internal-Token": TOKEN}).status_code == 422
    assert client.post("/internal/v1/analyses", json={**_body(), "input": {"context": {}}},
                       headers={"X-Internal-Token": TOKEN}).status_code == 422
    assert rec.calls == []


def test_agent_failure_reports_fail(env, monkeypatch):
    client, rec = env

    class Boom:
        def run(self, inp):
            raise AgentFailure(AgentError(code="MODEL_TIMEOUT", message="timeout", stage="agent2", retryable=True))

    monkeypatch.setattr(agent2_routes, "get_agent2", lambda: Boom())
    client.post("/internal/v1/analyses", json=_body(), headers={"X-Internal-Token": TOKEN})
    assert rec.calls[0][0] == "fail" and rec.calls[0][2] == ("MODEL_TIMEOUT", True)


def test_superseded_baseline_reports_version_conflict(env):
    client, rec = env
    old = {**INPUTS[CASE], "context": {**INPUTS[CASE]["context"], "baseline_id": "P-COM-101:BL-1"}}
    client.post("/internal/v1/analyses", json=_body(old), headers={"X-Internal-Token": TOKEN})
    assert rec.calls[0][0] == "fail" and rec.calls[0][2] == ("VERSION_CONFLICT", False)


def test_unexpected_error_still_reports(env, monkeypatch):
    client, rec = env

    class Crash:
        def run(self, inp):
            raise RuntimeError("bug")

    monkeypatch.setattr(agent2_routes, "get_agent2", lambda: Crash())
    client.post("/internal/v1/analyses", json=_body(), headers={"X-Internal-Token": TOKEN})
    assert rec.calls[0][0] == "fail" and rec.calls[0][2] == ("INTERNAL_ERROR", True)


def test_report_retries_only_retryable_errors(env, monkeypatch):
    client, _ = env
    rec = Recorder(AnalysisReportError("503", retryable=True, status_code=503))
    monkeypatch.setattr(internal_analysis_routes, "build_client", lambda _s: rec)
    client.post("/internal/v1/analyses", json=_body(), headers={"X-Internal-Token": TOKEN})
    assert len(rec.calls) == internal_analysis_routes.REPORT_RETRIES

    rec = Recorder(AnalysisReportError("400", retryable=False, status_code=400))
    monkeypatch.setattr(internal_analysis_routes, "build_client", lambda _s: rec)
    client.post("/internal/v1/analyses", json=_body(), headers={"X-Internal-Token": TOKEN})
    assert len(rec.calls) == 1


# ---- 결과 전달 클라이언트 ----
def _client(handler):
    http = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(handler))
    return AnalysisResultClient("http://backend", "tok", path=settings.backend_analysis_result_path, http=http)


def test_client_posts_complete_and_fail():
    seen, ai_id = [], uuid4()
    c = _client(lambda r: seen.append(r) or httpx.Response(200, json={}))
    c.complete(ai_id, {"status": "READY_FOR_REVIEW"})
    c.fail(ai_id, "MODEL_TIMEOUT", True)
    assert [r.url.path for r in seen] == [f"/internal/v1/ai-analyses/{ai_id}/complete",
                                          f"/internal/v1/ai-analyses/{ai_id}/fail"]
    assert all(r.headers["X-Internal-Token"] == "tok" for r in seen)
    assert httpx.Response(200, content=seen[1].content).json() == {"errorCode": "MODEL_TIMEOUT", "retryable": True}


@pytest.mark.parametrize("code,retryable", [(500, True), (429, True), (400, False), (409, False)])
def test_client_error_classification(code, retryable):
    with pytest.raises(AnalysisReportError) as e:
        _client(lambda r: httpx.Response(code, text="x")).complete(uuid4(), {})
    assert e.value.retryable is retryable
