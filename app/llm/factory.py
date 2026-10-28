"""Provider selection and configuration validation for LLM clients."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.llm.base import LlmClient
from app.llm.bedrock import BedrockLlm
from app.llm.fake import FakeLlm
from app.llm.local import LocalLlm


class LlmConfigError(ValueError):
    """Raised when a selected provider is missing required configuration."""


def build_llm(settings: Settings) -> LlmClient:
    """Create the configured provider without silently switching providers."""
    provider = settings.llm_provider
    if provider == "fake":
        return FakeLlm(
            responses=_fake_responses(settings.llm_fake_responses),
            model_id=settings.llm_model or "fake",
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    if provider == "local":
        if settings.llm_local_json_mode not in {"prompt", "schema"}:
            raise LlmConfigError("Invalid setting: LLM_LOCAL_JSON_MODE")
        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", settings.llm_base_url),
                ("LLM_MODEL", settings.llm_model),
            )
            if not value
        ]
        if missing:
            raise LlmConfigError(f"Missing required setting(s): {', '.join(missing)}")
        return LocalLlm(
            base_url=settings.llm_base_url,
            model_id=settings.llm_model,
            api_key=settings.llm_api_key,
            json_mode=settings.llm_local_json_mode,
            read_timeout_sec=settings.llm_read_timeout_sec,
            connect_timeout_sec=settings.llm_connect_timeout_sec,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    if provider == "bedrock":
        missing = [
            name
            for name, value in (
                ("LLM_MODEL", settings.llm_model),
                ("AWS_REGION", settings.aws_region),
            )
            if not value
        ]
        if missing:
            raise LlmConfigError(f"Missing required setting(s): {', '.join(missing)}")
        return BedrockLlm(
            model_id=settings.llm_model,
            region_name=settings.aws_region,
            read_timeout_sec=settings.llm_read_timeout_sec,
            connect_timeout_sec=settings.llm_connect_timeout_sec,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    raise ValueError("Unknown LLM_PROVIDER")


def _fake_responses(path: str | None) -> list[dict[str, Any] | str | Exception]:
    """Load optional offline fake responses from a JSON file."""
    if not path:
        return []
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LlmConfigError("Could not load LLM_FAKE_RESPONSES") from None
    if not isinstance(rows, list):
        raise LlmConfigError("LLM_FAKE_RESPONSES must contain a JSON list")

    responses: list[dict[str, Any] | str | Exception] = []
    for row in rows:
        if isinstance(row, dict) and set(row) == {"error"} and isinstance(row["error"], str):
            responses.append(_fake_error(row["error"]))
        elif isinstance(row, (dict, str)):
            responses.append(row)
        else:
            raise LlmConfigError("LLM_FAKE_RESPONSES contains an unsupported response")
    return responses


def _fake_error(name: str) -> Exception:
    """Create a safe provider exception for a configured fake failure."""
    if name == "timeout":
        return httpx.ReadTimeout("fake timeout")
    if name == "throttle":
        return ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "fake throttle"}},
            "Converse",
        )
    if name == "auth":
        return ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "fake auth"}},
            "Converse",
        )
    raise LlmConfigError("LLM_FAKE_RESPONSES contains an unsupported error")
