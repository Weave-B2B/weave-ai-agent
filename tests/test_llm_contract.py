import json
import os
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from unittest.mock import patch

import httpx
import pytest
from botocore.session import Session
from botocore.stub import ANY, Stubber
from botocore.exceptions import ReadTimeoutError
from pydantic import BaseModel

from app.agents.base import AgentFailure
from app.llm.base import StructuredLlm
from app.llm.bedrock import BedrockLlm, _inline_refs
from app.llm.fake import FakeLlm
from app.llm.local import LocalLlm
from schema.schemas import Agent1Output


class Reply(BaseModel):
    answer: str


@dataclass(frozen=True)
class HttpFailure:
    status_code: int


@dataclass(frozen=True)
class AwsFailure:
    code: str
    status_code: int


@dataclass
class Harness:
    client: StructuredLlm
    requests: list[dict[str, Any]]


def _bedrock_client():
    with patch.dict(
        os.environ,
        {
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            "AWS_CONFIG_FILE": os.devnull,
        },
        clear=True,
    ):
        session = Session()
        return session.create_client(
            "bedrock-runtime",
            region_name="ap-northeast-2",
            endpoint_url="https://bedrock-runtime.ap-northeast-2.amazonaws.com",
        )


@contextmanager
def _provider(provider: str, outcomes: list[Any]) -> Iterator[Harness]:
    requests: list[dict[str, Any]] = []
    if provider == "fake":
        fake_outcomes = [
            httpx.ReadTimeout("test timeout")
            if isinstance(outcome, AwsFailure) and outcome.code == "ModelTimeoutException"
            else httpx.ConnectError("test connection")
            if isinstance(outcome, AwsFailure) and outcome.code == "ServiceUnavailableException"
            else _client_error(outcome)
            if isinstance(outcome, AwsFailure)
            else outcome
            for outcome in outcomes
        ]
        client = FakeLlm(fake_outcomes, model_id="test-model")
        yield Harness(client, requests)
        requests.extend(client.calls)
        return

    if provider == "local":
        pending = deque(outcomes)

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "json": json.loads(request.content),
                }
            )
            outcome = pending.popleft()
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, HttpFailure):
                return httpx.Response(outcome.status_code, request=request, json={"error": "stub"})
            content = None if outcome is None else outcome if isinstance(outcome, str) else json.dumps(outcome)
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = LocalLlm(
            base_url="http://llm.test/v1",
            model_id="test-model",
            client=http_client,
        )
        try:
            yield Harness(client, requests)
        finally:
            http_client.close()
        return

    if provider == "bedrock":
        raw_client = _bedrock_client()
        stubber = Stubber(raw_client)
        for outcome in outcomes:
            expected = {
                "modelId": "test-model",
                "system": ANY,
                "messages": ANY,
                "inferenceConfig": ANY,
                "toolConfig": ANY,
            }
            if isinstance(outcome, AwsFailure):
                stubber.add_client_error(
                    "converse",
                    service_error_code=outcome.code,
                    service_message="stubbed failure",
                    http_status_code=outcome.status_code,
                    expected_params=expected,
                )
            else:
                content = []
                if outcome is not None:
                    payload = outcome if isinstance(outcome, dict) else {"answer": outcome}
                    content = [
                        {
                            "toolUse": {
                                "toolUseId": "test-tool-use",
                                "name": "submit",
                                "input": payload,
                            }
                        }
                    ]
                stubber.add_response(
                    "converse",
                    {
                        "output": {
                            "message": {
                                "role": "assistant",
                                "content": content,
                            }
                        },
                        "stopReason": "tool_use" if content else "end_turn",
                        "usage": {"inputTokens": 3, "outputTokens": 2, "totalTokens": 5},
                        "metrics": {"latencyMs": 1},
                        "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "test"},
                    },
                    expected_params=expected,
                )
        stubber.activate()

        class CountingClient:
            def converse(self, **kwargs: Any) -> dict[str, Any]:
                requests.append(kwargs)
                return raw_client.converse(**kwargs)

        client = BedrockLlm(model_id="test-model", region_name="ap-northeast-2", client=CountingClient())
        try:
            yield Harness(client, requests)
            stubber.assert_no_pending_responses()
        finally:
            stubber.deactivate()
            raw_client.close()
        return

    raise AssertionError(f"unknown provider {provider}")


def _client_error(outcome: AwsFailure) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError(
        {
            "Error": {"Code": outcome.code, "Message": "stubbed failure"},
            "ResponseMetadata": {"HTTPStatusCode": outcome.status_code},
        },
        "Converse",
    )


def _outcome(provider: str, kind: str) -> Any:
    if kind == "valid":
        return {"answer": "ok"}
    if kind == "invalid":
        return {"unexpected_field": "secret-output"}
    if kind == "missing":
        return None if provider == "bedrock" else ""
    if kind == "timeout":
        return (
            AwsFailure("ModelTimeoutException", 408)
            if provider == "bedrock"
            else httpx.ReadTimeout("secret-timeout")
        )
    if kind == "throttle":
        return (
            AwsFailure("ThrottlingException", 429)
            if provider == "bedrock"
            else HttpFailure(429)
            if provider == "local"
            else AwsFailure("ThrottlingException", 429)
        )
    if kind == "unavailable":
        return (
            AwsFailure("ServiceUnavailableException", 503)
            if provider == "bedrock"
            else HttpFailure(503)
            if provider == "local"
            else AwsFailure("ServiceUnavailableException", 503)
        )
    if kind == "server_error":
        return (
            AwsFailure("InternalServerException", 500)
            if provider != "local"
            else HttpFailure(500)
        )
    if kind == "auth":
        return (
            AwsFailure("AccessDeniedException", 403)
            if provider == "bedrock"
            else HttpFailure(403)
            if provider == "local"
            else AwsFailure("AccessDeniedException", 403)
        )
    if kind == "unauthorized":
        return (
            AwsFailure("AccessDeniedException", 403)
            if provider != "local"
            else HttpFailure(401)
        )
    raise AssertionError(kind)


@pytest.mark.parametrize("provider", ["fake", "local", "bedrock"])
def test_normal_response_contract(provider: str) -> None:
    with _provider(provider, [_outcome(provider, "valid")]) as harness:
        result = harness.client.complete_structured(
            system="system prompt",
            user="user prompt",
            schema=Reply,
            tool_name="submit",
        )

    assert isinstance(result.parsed, Reply)
    assert result.parsed.answer == "ok"
    assert result.usage.input_tokens == (0 if provider == "fake" else 3)
    assert result.usage.output_tokens == (0 if provider == "fake" else 2)
    assert result.latency_ms >= 0
    assert result.provider == provider
    assert result.model_id == "test-model"
    assert result.label == f"{provider}:test-model"
    assert result.retried is False
    if provider == "fake":
        assert harness.requests[0]["schema"] == "Reply"
        assert harness.requests[0]["tool_name"] == "submit"


@pytest.mark.parametrize("provider", ["fake", "local", "bedrock"])
def test_invalid_response_is_retried_once_with_redacted_summary(provider: str) -> None:
    with _provider(
        provider,
        [_outcome(provider, "invalid"), _outcome(provider, "valid")],
    ) as harness:
        result = harness.client.complete_structured(
            system="private system prompt",
            user="private user prompt",
            schema=Reply,
        )

    assert result.retried is True
    assert len(harness.requests) == 2
    assert result.usage.input_tokens == (0 if provider == "fake" else 6)
    assert result.usage.output_tokens == (0 if provider == "fake" else 4)
    retry_user = _request_user(provider, harness)
    assert "이전 응답이 스키마에 맞지 않았다:" in retry_user
    assert "answer:missing" in retry_user
    assert "secret-output" not in retry_user
    assert "private system prompt" not in retry_user


@pytest.mark.parametrize("provider", ["fake", "local", "bedrock"])
@pytest.mark.parametrize("first_kind", ["invalid", "missing"])
def test_invalid_response_twice_fails_without_exposing_payload(
    provider: str,
    first_kind: str,
) -> None:
    with _provider(
        provider,
        [_outcome(provider, first_kind), _outcome(provider, first_kind)],
    ) as harness:
        with pytest.raises(AgentFailure) as caught:
            harness.client.complete_structured(
                system="PRIVATE_SYSTEM",
                user="PRIVATE_USER",
                schema=Reply,
            )

    assert caught.value.error.code == "OUTPUT_INVALID"
    assert caught.value.error.retryable is True
    assert len(harness.requests) == 2
    assert "PRIVATE_SYSTEM" not in caught.value.error.message
    assert "PRIVATE_USER" not in caught.value.error.message
    assert "secret-output" not in caught.value.error.message


@pytest.mark.parametrize("provider", ["fake", "local", "bedrock"])
@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "expected_retryable"),
    [
        ("timeout", "MODEL_TIMEOUT", True),
        ("throttle", "MODEL_TIMEOUT", True),
        ("unavailable", "MODEL_TIMEOUT", True),
        ("server_error", "MODEL_TIMEOUT", True),
        ("auth", "INTERNAL_ERROR", False),
        ("unauthorized", "INTERNAL_ERROR", False),
    ],
)
def test_provider_failures_are_mapped_without_format_retry(
    provider: str,
    failure_kind: str,
    expected_code: str,
    expected_retryable: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _provider(provider, [_outcome(provider, failure_kind)]) as harness:
        with pytest.raises(AgentFailure) as caught:
            harness.client.complete_structured(
                system="TOP_SECRET_SYSTEM",
                user="TOP_SECRET_USER",
                schema=Reply,
            )

    assert caught.value.error.code == expected_code
    assert caught.value.error.retryable is expected_retryable
    assert caught.value.error.stage is None
    assert len(harness.requests) == 1
    assert "TOP_SECRET" not in caught.value.error.message
    assert "TOP_SECRET" not in caplog.text


def _request_user(provider: str, harness: Harness) -> str:
    """Get the user prompt from a provider's recorded request."""
    if provider == "fake":
        return harness.requests[-1]["user"]
    if provider == "local":
        return harness.requests[-1]["json"]["messages"][1]["content"]
    return harness.requests[-1]["messages"][0]["content"][0]["text"]


def test_local_json_modes_auth_header_usage_and_code_fence() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "headers": dict(request.headers),
                "json": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "```json\n{\"answer\":\"ok\"}\n```"}}]},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = LocalLlm(
        base_url="http://llm.test/v1/",
        model_id="model-from-settings",
        api_key="test-secret-key",
        json_mode="schema",
        client=http_client,
    )
    try:
        result = client.complete_structured(
            system="system",
            user="user",
            schema=Reply,
            tool_name="answer_tool",
            max_tokens=123,
            temperature=0.25,
        )
    finally:
        http_client.close()

    assert result.parsed.answer == "ok"
    assert result.usage.input_tokens == result.usage.output_tokens == 0
    assert captured[0]["headers"]["authorization"] == "Bearer test-secret-key"
    assert captured[0]["json"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "answer_tool",
            "schema": Reply.model_json_schema(),
            "strict": True,
        },
    }
    assert captured[0]["json"]["max_tokens"] == 123
    assert captured[0]["json"]["temperature"] == 0.25


def test_local_prompt_mode_adds_schema_instruction_and_optional_auth() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"headers": dict(request.headers), "json": json.loads(request.content)})
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"content": '{"answer":"ok"}', "finish_reason": "stop"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 4},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = LocalLlm(
        base_url="http://llm.test",
        model_id="model-from-settings",
        client=http_client,
    )
    try:
        result = client.complete_structured(system="system", user="user", schema=Reply)
    finally:
        http_client.close()

    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 4
    assert "JSON 스키마" in captured[0]["json"]["messages"][0]["content"]
    assert "response_format" not in captured[0]["json"]
    assert "authorization" not in captured[0]["headers"]


def test_bedrock_schema_is_inlined_and_request_settings_are_forwarded() -> None:
    raw_client = _bedrock_client()
    stubber = Stubber(raw_client)
    expected_input_schema = _inline_refs(Agent1Output.model_json_schema())
    stubber.add_response(
        "converse",
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool-id",
                                "name": "agent_output",
                                "input": {
                                    "context": {
                                        "project_id": "project",
                                        "case_id": "case",
                                        "request_revision": 1,
                                    },
                                    "status": "READY",
                                    "items": [],
                                },
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
            "metrics": {"latencyMs": 1},
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "test"},
        },
        expected_params={
            "modelId": "configured-model-id",
            "system": [{"text": "system"}],
            "messages": [{"role": "user", "content": [{"text": "user"}]}],
            "inferenceConfig": {"maxTokens": 321, "temperature": 0.4},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "agent_output",
                            "description": "Return the requested structured response.",
                            "inputSchema": {"json": expected_input_schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": "agent_output"}},
            },
        },
    )
    stubber.activate()
    client = BedrockLlm(
        model_id="configured-model-id",
        region_name="ap-northeast-2",
        client=raw_client,
        max_tokens=321,
        temperature=0.4,
    )
    try:
        result = client.complete_structured(
            system="system",
            user="user",
            schema=Agent1Output,
            tool_name="agent_output",
        )
        assert result.parsed.status == "READY"
        stubber.assert_no_pending_responses()
    finally:
        stubber.deactivate()
        raw_client.close()

    assert not _contains_key(expected_input_schema, "$ref")


def test_bedrock_client_uses_single_sdk_attempt_and_configured_timeouts(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def create_client(service_name: str, **kwargs: Any) -> object:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.llm.bedrock.boto3.client", create_client)
    BedrockLlm(
        model_id="configured-model-id",
        region_name="ap-northeast-2",
        read_timeout_sec=41,
        connect_timeout_sec=4,
    )

    config = captured["config"]
    assert captured["service_name"] == "bedrock-runtime"
    assert captured["region_name"] == "ap-northeast-2"
    assert config.read_timeout == 41
    assert config.connect_timeout == 4
    assert config.retries["max_attempts"] == 1


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("secret connection details"),
        ReadTimeoutError(endpoint_url="secret endpoint", error="secret timeout details"),
    ],
)
def test_connection_and_botocore_timeout_errors_are_retryable_without_details(
    error: Exception,
) -> None:
    client = FakeLlm([error])

    with pytest.raises(AgentFailure) as caught:
        client.complete_structured(system="private", user="private", schema=Reply)

    assert caught.value.error.code == "MODEL_TIMEOUT"
    assert caught.value.error.retryable is True
    assert "secret" not in caught.value.error.message


def _contains_key(value: Any, key: str) -> bool:
    """Return whether a nested JSON-like value contains a given dictionary key."""
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


@pytest.mark.local
def test_optional_local_llm_integration() -> None:
    import os

    base_url = os.getenv("LLM_BASE_URL", "")
    model_id = os.getenv("LLM_MODEL", "")
    if os.getenv("RUN_LOCAL_LLM_INTEGRATION") != "1" or not base_url or not model_id:
        pytest.skip("set RUN_LOCAL_LLM_INTEGRATION=1, LLM_BASE_URL, and LLM_MODEL")

    client = LocalLlm(
        base_url=base_url,
        model_id=model_id,
        api_key=os.getenv("LLM_API_KEY", ""),
    )
    result = client.complete_structured(
        system="Return the requested JSON object.",
        user='Return {"answer":"ok"}.',
        schema=Reply,
    )
    assert result.parsed.answer == "ok"


@pytest.mark.bedrock
def test_optional_bedrock_integration() -> None:
    import os

    model_id = os.getenv("LLM_MODEL", "")
    if os.getenv("RUN_BEDROCK_LLM_INTEGRATION") != "1" or not model_id:
        pytest.skip("set RUN_BEDROCK_LLM_INTEGRATION=1 and LLM_MODEL")

    from app.core.config import settings

    client = BedrockLlm(model_id=model_id, region_name=settings.aws_region)
    result = client.complete_structured(
        system="Return the requested JSON object.",
        user='Return {"answer":"ok"}.',
        schema=Reply,
    )
    assert result.parsed.answer == "ok"
