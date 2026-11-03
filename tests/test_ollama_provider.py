import json

import httpx
from pydantic import BaseModel

from app.llm.ollama import OllamaLlm


class Reply(BaseModel):
    answer: str


def test_ollama_structured_tool_response() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit",
                                "arguments": {"answer": "hello"},
                            }
                        }
                    ],
                },
                "done_reason": "stop",
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    llm = OllamaLlm(
        base_url="http://localhost:11434/",
        model_id="gpt-oss:20b",
        client=http_client,
    )
    result = llm.complete_structured(
        system="system",
        user="user",
        schema=Reply,
    )

    assert result.parsed.answer == "hello"
    assert result.provider == "ollama"
    assert result.model_id == "gpt-oss:20b"
    assert result.usage.input_tokens == 4
    assert result.usage.output_tokens == 2
    assert requests[0]["model"] == "gpt-oss:20b"
    assert requests[0]["stream"] is False
    assert requests[0]["format"] == Reply.model_json_schema()
    assert requests[0]["tools"][0]["function"]["name"] == "submit"
    http_client.close()


def test_ollama_content_fallback_is_structurally_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "message": {"role": "assistant", "content": '{"answer":"fallback"}'},
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 1,
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    llm = OllamaLlm(base_url="http://localhost:11434", model_id="test-model", client=http_client)
    result = llm.complete_structured(system="system", user="user", schema=Reply)
    assert result.parsed.answer == "fallback"
    http_client.close()
