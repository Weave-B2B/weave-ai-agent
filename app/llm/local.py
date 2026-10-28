"""OpenAI-compatible local LLM provider."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel

from app.llm.base import RawResponse, StructuredLlm, Usage


class LocalLlm(StructuredLlm):
    """Call a local OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key: str = "",
        json_mode: str = "prompt",
        read_timeout_sec: float = 50,
        connect_timeout_sec: float = 5,
        client: httpx.Client | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        """Configure the local endpoint, request mode, and optional HTTP client."""
        if json_mode not in {"prompt", "schema"}:
            raise ValueError("LLM_LOCAL_JSON_MODE must be prompt or schema")
        super().__init__(
            provider="local",
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.json_mode = json_mode
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(read_timeout_sec, connect=connect_timeout_sec)
        )

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
        """Send one chat completion request and extract its text response."""
        selected_system = system
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": selected_system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.json_mode == "prompt":
            selected_system = (
                f"{system}\n\n다음 JSON 스키마에 맞는 JSON 객체 하나만 출력. "
                f"설명·코드펜스 금지\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
            )
            body["messages"][0]["content"] = selected_system
        elif self.json_mode == "schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": tool_name,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        response_body = response.json()
        if not isinstance(response_body, dict):
            raise TypeError("local response must be an object")

        choices = response_body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        payload = message.get("content") if isinstance(message, dict) else None
        usage_body = response_body.get("usage")
        if usage_body is None:
            usage_body = {}
        if not isinstance(usage_body, dict):
            raise TypeError("local usage must be an object")

        return RawResponse(
            payload=payload,
            usage=Usage(
                input_tokens=_token_count(usage_body, "prompt_tokens"),
                output_tokens=_token_count(usage_body, "completion_tokens"),
            ),
            stop_reason=choice.get("finish_reason") if isinstance(choice, dict) else None,
        )


def _token_count(usage: dict[str, Any], key: str) -> int:
    """Return a validated token count, defaulting only when the field is absent."""
    value = usage.get(key, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("invalid token count")
    return value
