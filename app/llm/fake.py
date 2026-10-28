"""Offline LLM provider for tests and local development."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentFailure
from app.llm.base import RawResponse, StructuredLlm
from schema.schemas import AgentError


class FakeLlm(StructuredLlm):
    """Return queued outputs without making network requests."""

    def __init__(
        self,
        responses: list[dict[str, Any] | str | Exception] | None = None,
        model_id: str = "fake",
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        """Initialize the provider with a finite queue of fake responses."""
        super().__init__(
            provider="fake",
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

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
        """Consume one queued response and record the request for assertions."""
        self.calls.append(
            {
                "system": system,
                "user": user,
                "schema": schema.__name__,
                "tool_name": tool_name,
            }
        )
        if not self._responses:
            raise AgentFailure(
                AgentError(
                    code="INTERNAL_ERROR",
                    message="fake 응답 부족",
                    stage=None,
                    retryable=False,
                )
            )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return RawResponse(payload=response)
