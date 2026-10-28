"""Provider-independent structured LLM client contracts and behavior."""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Generic, Protocol, TypeVar

import httpx
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.base import AgentFailure
from schema.schemas import AgentError

T = TypeVar("T", bound=BaseModel)


class Usage(BaseModel):
    """Token counts returned by an LLM provider."""

    input_tokens: int = 0
    output_tokens: int = 0


class RawResponse(BaseModel):
    """Unvalidated provider output and usage metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    payload: dict[str, Any] | str | None
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str | None = None


class LlmResult(BaseModel, Generic[T]):
    """Validated structured output and request metadata."""

    parsed: T
    usage: Usage
    latency_ms: int
    provider: str
    model_id: str
    retried: bool

    @property
    def label(self) -> str:
        """Return the provider and model identifier for output metadata."""
        return f"{self.provider}:{self.model_id}"


class LlmClient(Protocol):
    """Common interface for provider-independent structured completions."""

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tool_name: str = "submit",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResult[T]:
        """Return a schema-validated completion from the selected provider."""
        ...


class StructuredLlm(ABC):
    """Shared validation, retry, timing, and error mapping for providers."""

    def __init__(
        self,
        *,
        provider: str,
        model_id: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        self.provider = provider
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature

    @abstractmethod
    def _call(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        tool_name: str,
        max_tokens: int,
        temperature: float,
    ) -> RawResponse:
        """Send one provider request and return its unvalidated response."""

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tool_name: str = "submit",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResult[T]:
        """Validate one structured completion, retrying invalid output once."""
        selected_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        selected_temperature = self.temperature if temperature is None else temperature
        started = time.perf_counter()
        usage = Usage()
        retry_user = user
        retried = False

        for attempt in range(2):
            try:
                raw = self._call(
                    system=system,
                    user=retry_user,
                    schema=schema,
                    tool_name=tool_name,
                    max_tokens=selected_max_tokens,
                    temperature=selected_temperature,
                )
            except AgentFailure:
                raise
            except Exception as exc:
                raise _agent_failure(exc) from None

            usage = Usage(
                input_tokens=usage.input_tokens + raw.usage.input_tokens,
                output_tokens=usage.output_tokens + raw.usage.output_tokens,
            )
            try:
                parsed = _parse_payload(raw.payload, schema)
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
                if attempt == 0:
                    retried = True
                    retry_user = f"{user}\n이전 응답이 스키마에 맞지 않았다: {_validation_summary(exc)}"
                    continue
                error = AgentError(
                    code="OUTPUT_INVALID",
                    message="structured response did not match the requested schema",
                    stage=None,
                    retryable=True,
                )
                raise AgentFailure(error) from None

            return LlmResult[T](
                parsed=parsed,
                usage=usage,
                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                provider=self.provider,
                model_id=self.model_id,
                retried=retried,
            )

        raise AssertionError("unreachable")


def _parse_payload(payload: dict[str, Any] | str | None, schema: type[T]) -> T:
    """Parse provider JSON text and validate it with the requested model."""
    if payload is None:
        raise ValueError("empty response")
    if isinstance(payload, str):
        text = payload.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1)
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise TypeError("structured response must be an object")
    return schema.model_validate(payload)


def _validation_summary(error: Exception) -> str:
    """Describe schema errors using paths and error types only."""
    if isinstance(error, ValidationError):
        parts = [
            f"{'.'.join(str(part) for part in item['loc']) or '$'}:{item['type']}"
            for item in error.errors(include_input=False, include_url=False)
        ]
    elif isinstance(error, json.JSONDecodeError):
        parts = ["$:json_invalid"]
    else:
        parts = ["$:invalid_payload"]
    return "; ".join(parts[:10])


def _agent_failure(error: Exception) -> AgentFailure:
    """Map provider exceptions to safe shared agent errors."""
    error_code = "INTERNAL_ERROR"
    retryable = False
    reason = "provider request failed"

    if isinstance(error, (httpx.TimeoutException, ReadTimeoutError, ConnectTimeoutError)):
        error_code, retryable, reason = "MODEL_TIMEOUT", True, "request timed out"
    elif isinstance(error, (httpx.TransportError, EndpointConnectionError, ConnectionClosedError)):
        error_code, retryable, reason = "MODEL_TIMEOUT", True, "connection failed"
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in {408, 429} or status >= 500:
            error_code, retryable, reason = "MODEL_TIMEOUT", True, "provider temporarily unavailable"
        else:
            reason = "provider rejected the request"
    elif isinstance(error, ClientError):
        service_code = error.response.get("Error", {}).get("Code", "")
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if service_code in {
            "ModelTimeoutException",
            "ThrottlingException",
            "ServiceUnavailableException",
            "ModelNotReadyException",
        } or status in {408, 429} or status >= 500:
            error_code, retryable, reason = "MODEL_TIMEOUT", True, "provider temporarily unavailable"
        elif service_code in {
            "AccessDeniedException",
            "ValidationException",
            "ResourceNotFoundException",
        }:
            reason = "provider rejected the request"

    return AgentFailure(
        AgentError(
            code=error_code,
            message=f"{type(error).__name__}: {reason}",
            stage=None,
            retryable=retryable,
        )
    )
