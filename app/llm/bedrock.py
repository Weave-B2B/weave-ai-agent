"""AWS Bedrock Converse API provider."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import boto3
from botocore.config import Config
from pydantic import BaseModel

from app.llm.base import RawResponse, StructuredLlm, Usage


class BedrockLlm(StructuredLlm):
    """Call Bedrock Converse with a required structured tool response."""

    def __init__(
        self,
        *,
        model_id: str,
        region_name: str,
        read_timeout_sec: float = 50,
        connect_timeout_sec: float = 5,
        client: Any | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        """Configure Bedrock Converse and an optional injected SDK client."""
        super().__init__(
            provider="bedrock",
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.region_name = region_name
        self.client = client or boto3.client(
            "bedrock-runtime",
            region_name=region_name,
            config=Config(
                read_timeout=read_timeout_sec,
                connect_timeout=connect_timeout_sec,
                retries={"max_attempts": 1},
            ),
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
        """Send one Converse request and extract the forced tool input."""
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool_name,
                            "description": "Return the requested structured response.",
                            "inputSchema": {"json": _inline_refs(schema.model_json_schema())},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": tool_name}},
            },
        )
        output = response.get("output", {}).get("message", {})
        content = output.get("content", [])
        tool_use = next(
            (
                block["toolUse"]
                for block in content
                if isinstance(block, dict)
                and isinstance(block.get("toolUse"), dict)
                and block["toolUse"].get("name") == tool_name
            ),
            None,
        )
        usage_body = response.get("usage", {})
        if not isinstance(usage_body, dict):
            raise TypeError("Bedrock usage must be an object")

        return RawResponse(
            payload=tool_use.get("input") if tool_use is not None else None,
            usage=Usage(
                input_tokens=_token_count(usage_body, "inputTokens"),
                output_tokens=_token_count(usage_body, "outputTokens"),
            ),
            stop_reason=response.get("stopReason"),
        )


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local JSON Schema references for Bedrock tool input schemas."""
    root = deepcopy(schema)

    def resolve_pointer(reference: str) -> Any:
        if not reference.startswith("#/"):
            raise ValueError("only local JSON Schema references are supported")
        value: Any = root
        for component in reference[2:].split("/"):
            key = component.replace("~1", "/").replace("~0", "~")
            value = value[key]
        return value

    def visit(value: Any, active_refs: frozenset[str]) -> Any:
        if isinstance(value, list):
            return [visit(item, active_refs) for item in value]
        if not isinstance(value, dict):
            return value

        reference = value.get("$ref")
        if reference is not None:
            if reference in active_refs:
                raise ValueError("recursive JSON Schema references are not supported")
            expanded = visit(resolve_pointer(reference), active_refs | {reference})
            siblings = {
                key: visit(item, active_refs)
                for key, item in value.items()
                if key != "$ref"
            }
            if isinstance(expanded, dict):
                return {**expanded, **siblings}
            return expanded

        return {
            key: visit(item, active_refs)
            for key, item in value.items()
            if key not in {"$defs", "definitions"}
        }

    return visit(root, frozenset())


def _token_count(usage: dict[str, Any], key: str) -> int:
    """Return a validated token count, defaulting only when the field is absent."""
    value = usage.get(key, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("invalid token count")
    return value
