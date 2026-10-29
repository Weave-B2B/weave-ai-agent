"""Gemini-compatible HTTP contracts only: never calls the remote service."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.draft import Agent3ImpactDraft
from app.agents.agent3.workflow import Agent3Workflow
from app.api import agent3_routes
from app.core.config import Settings
from app.llm.factory import build_llm
from app.llm.local import LocalLlm
from app.main import app
from prototype.run_demo import GEMINI_BASE_URL, GEMINI_MODEL
from test_agent3_agent import a3_input, a3_payload  # noqa: F401
from test_agent3_workflow import edit_request, review_request


def client_for(handler):
    return LocalLlm(base_url=GEMINI_BASE_URL, model_id=GEMINI_MODEL, api_key="mock-private-key",
        json_mode="schema", client=httpx.Client(transport=httpx.MockTransport(handler)))


def assert_strict_schema(value):
    if isinstance(value, dict):
        assert not {"$ref", "$defs", "default"} & value.keys()
        if value.get("type") == "object":
            assert value["additionalProperties"] is False
            assert value["required"] == list(value["properties"])
        for child in value.values():
            assert_strict_schema(child)
    elif isinstance(value, list):
        for child in value:
            assert_strict_schema(child)


def test_gemini_http_prepare_edit_review_draft_contract(a3_input, a3_payload, monkeypatch):
    calls = []
    # The mocked provider emits every strict-schema property, including defaults.
    payload = Agent3ImpactDraft.model_validate(a3_payload).model_dump(mode="json")
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert str(request.url) == GEMINI_BASE_URL + "chat/completions"
        assert request.headers["authorization"] == "Bearer mock-private-key"
        assert body["model"] == GEMINI_MODEL
        assert body["stream"] is False
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert_strict_schema(body["response_format"]["json_schema"]["schema"])
        assert "RC-SYNTH" not in body["messages"][1]["content"]
        assert "mock-private-key" not in json.dumps(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}})
    llm = client_for(handler)
    # Reuse one workflow across every API call, as the real cached route does.
    workflow = Agent3Workflow(LlmAgent3(llm))
    monkeypatch.setattr(agent3_routes, "get_workflow", lambda: workflow)
    with TestClient(app) as client:
        prepared = client.post("/agent3/impacts", json=a3_input.model_dump(mode="json"))
        assert prepared.status_code == 200
        view = prepared.json()
        assert view["draft"] is None and view["impact"]["meta"]["model"] == "local:" + GEMINI_MODEL
        base = "/agent3/impacts/" + view["session_id"]
        edited = client.post(base + "/edit", json=edit_request(view).model_dump(mode="json")).json()
        assert edited["impact"]["estimates"][0]["totals"]["price"] == 4000000
        assert edited["draft"] is None
        final = client.post(base + "/review", json=review_request(edited).model_dump(mode="json"))
        assert final.status_code == 200 and final.json()["phase"] == "DRAFT_READY"
        assert "4,000,000원" in final.json()["draft"]["cost"]
        assert "mock-private-key" not in final.text
        assert len(calls) == 1  # edits and approved template do not call a model
    llm.client.close()


@pytest.mark.parametrize("outcome,code,retryable,count", [("malformed", "OUTPUT_INVALID", True, 2),
    ("timeout", "MODEL_TIMEOUT", True, 1), ("auth", "INTERNAL_ERROR", False, 1), ("schema-rejected", "INTERNAL_ERROR", False, 1)])
def test_gemini_failures_surface_without_fake_fallback(a3_input, monkeypatch, outcome, code, retryable, count):
    calls = []
    def handler(request):
        calls.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("mock-private-key provider details", request=request)
        if outcome in {"auth", "schema-rejected"}:
            return httpx.Response(401 if outcome == "auth" else 400, json={"error": "mock-private-key provider details"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "not JSON mock-private-key"}}]})
    llm = client_for(handler)
    workflow = Agent3Workflow(LlmAgent3(llm))
    monkeypatch.setattr(agent3_routes, "get_workflow", lambda: workflow)
    with TestClient(app) as client:
        response = client.post("/agent3/impacts", json=a3_input.model_dump(mode="json"))
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == code
        assert response.json()["detail"]["retryable"] is retryable
        assert "mock-private-key" not in response.text
        assert len(calls) == count
        assert not workflow._sessions
    llm.client.close()


def test_existing_factory_constructs_gemini_compatible_client_without_sdk():
    settings = Settings(_env_file=None, llm_provider="local", llm_model=GEMINI_MODEL,
        llm_api_key="mock-private-key", llm_base_url=GEMINI_BASE_URL, llm_local_json_mode="schema")
    client = build_llm(settings)
    assert isinstance(client, LocalLlm) and client.model_id == GEMINI_MODEL
    client.client.close()
