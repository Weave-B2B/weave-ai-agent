"""OpenAI Responses API adapter for the shared structured agent interface."""

import httpx
from pydantic import BaseModel

from app.llm.base import RawResponse, StructuredLlm, Usage
from app.llm.local import _request_schema, _token_count


class OpenAIResponsesLlm(StructuredLlm):
    def __init__(self, *, model_id: str, api_key: str, read_timeout_sec: float = 50,
                 connect_timeout_sec: float = 5, client: httpx.Client | None = None,
                 max_tokens: int = 4096, temperature: float = 0.0):
        super().__init__(provider="openai", model_id=model_id, max_tokens=max_tokens, temperature=temperature)
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=httpx.Timeout(read_timeout_sec, connect=connect_timeout_sec))

    def _call(self, *, system: str, user: str, schema: type[BaseModel], tool_name: str,
              max_tokens: int, temperature: float) -> RawResponse:
        body = {
            "model": self.model_id,
            "instructions": system,
            "input": user,
            "max_output_tokens": max_tokens,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": tool_name, "strict": True,
                                 "schema": _request_schema(schema, "https://generativelanguage.googleapis.com")}},
        }
        response = self.client.post("https://api.openai.com/v1/responses",
                                    headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("output", []), list):
            raise ValueError("invalid response")
        texts = [part.get("text") for item in data.get("output", []) if isinstance(item, dict)
                 for part in (item.get("content") or []) if isinstance(part, dict) and part.get("type") == "output_text"]
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("invalid usage")
        payload = "".join(t for t in texts if isinstance(t, str)) if data.get("status") == "completed" else None
        return RawResponse(payload=payload or None,
                           usage=Usage(input_tokens=_token_count(usage, "input_tokens"),
                                       output_tokens=_token_count(usage, "output_tokens")),
                           stop_reason=data.get("status"))
