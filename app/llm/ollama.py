"""Ollama native chat provider for the shared structured LLM interface."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel

from app.llm.base import RawResponse, StructuredLlm, Usage


class OllamaLlm(StructuredLlm):
    """Call Ollama's native /api/chat endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        read_timeout_sec: float = 50,
        connect_timeout_sec: float = 5,
        num_predict: int = 2048,
        keep_alive: str = "30m",
        client: httpx.Client | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(
            provider="ollama",
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.base_url = base_url.rstrip("/")
        self.num_predict = num_predict
        self.keep_alive = keep_alive
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
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "think": False,
            "format": schema.model_json_schema(),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": "Return the requested structured response.",
                        "parameters": schema.model_json_schema(),
                    },
                }
            ],
            "options": {
                "temperature": temperature,
                "num_predict": min(max_tokens, self.num_predict),
            },
        }
        response = self.client.post(f"{self.base_url}/api/chat", json=body)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("ollama response must be an object")

        message = data.get("message")
        if not isinstance(message, dict):
            raise ValueError("invalid ollama message")

        payload: dict[str, Any] | str | None = None
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict) or function.get("name") != tool_name:
                    continue
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    payload = arguments
                    break
                if isinstance(arguments, str):
                    payload = arguments
                    break

        if payload is None:
            content = message.get("content")
            payload = content if isinstance(content, str) else None

        if payload is not None:
            payload = _normalize_schema_payload(payload, schema.model_json_schema())

        prompt_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)
        if not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool) or prompt_tokens < 0:
            raise ValueError("invalid Ollama prompt token count")
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 0:
            raise ValueError("invalid Ollama output token count")

        return RawResponse(
            payload=payload,
            usage=Usage(input_tokens=prompt_tokens, output_tokens=output_tokens),
            stop_reason=data.get("done_reason"),
        )


def _normalize_schema_payload(payload: dict[str, Any] | str, schema: dict[str, Any]) -> dict[str, Any] | str:
    """Parse Ollama JSON text and remove fields not declared by the schema."""
    if isinstance(payload, str):
        text = payload.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return payload
    else:
        parsed = payload

    if not isinstance(parsed, dict):
        return payload
    return _strip_schema_extra_fields(parsed, schema)


def _strip_schema_extra_fields(value: Any, schema: dict[str, Any]) -> Any:
    """Remove nested keys that the Pydantic response schema rejects."""
    defs = schema.get("$defs", {})

    def resolve(node: dict[str, Any]) -> dict[str, Any]:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            resolved = defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(resolved, dict):
                return resolved
        return node

    def clean(current: Any, node: dict[str, Any] | None) -> Any:
        if node is None:
            return current
        node = resolve(node)

        if isinstance(current, list):
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                return [clean(item, item_schema) for item in current]
            return current

        if not isinstance(current, dict):
            return current

        properties = node.get("properties")
        if not isinstance(properties, dict):
            return current

        normalized: dict[str, Any] = {}
        for key, child in current.items():
            if key not in properties:
                continue
            child_schema = properties[key] if isinstance(properties[key], dict) else None
            cleaned = clean(child, child_schema)
            if key == "item_type" and cleaned == "feature":
                cleaned = "function"
            normalized[key] = cleaned
        return normalized

    return clean(value, schema)
