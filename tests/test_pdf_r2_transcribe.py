"""새 PDF 파서의 페이지 전사 함수 선택·Bedrock 호출 처리 (docling 없이 실행)."""
import pytest
from botocore.exceptions import ClientError

from app.indexing import pdf_r2


class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _ok(text, stop="end_turn"):
    return {"output": {"message": {"content": [{"text": text}]}}, "stopReason": stop}


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Converse")


def test_bedrock_default_model_and_image_payload():
    client = FakeBedrock(_ok("```markdown\n# 제1조 목적\n본문\n```"))
    transcribe = pdf_r2.build_transcriber("bedrock", "", client=client)
    assert transcribe(b"PNG", 3) == "# 제1조 목적\n본문"
    call = client.calls[0]
    assert call["modelId"] == pdf_r2.DEFAULT_MODELS["bedrock"]
    image, text = call["messages"][0]["content"]
    assert image == {"image": {"format": "png", "source": {"bytes": b"PNG"}}}
    assert text["text"] == pdf_r2._PROMPT


def test_bedrock_model_override():
    client = FakeBedrock(_ok("본문"))
    pdf_r2.build_transcriber("bedrock", " my-model ", client=client)(b"PNG", 1)
    assert client.calls[0]["modelId"] == "my-model"


@pytest.mark.parametrize("code, retryable", [("ThrottlingException", True), ("ServiceUnavailableException", True),
                                             ("AccessDeniedException", False), ("ValidationException", False)])
def test_bedrock_errors_map_to_retryable(code, retryable):
    transcribe = pdf_r2.build_transcriber("bedrock", client=FakeBedrock(error=_client_error(code)))
    with pytest.raises(pdf_r2.PageFallbackError) as info:
        transcribe(b"PNG", 2)
    assert info.value.retryable is retryable


@pytest.mark.parametrize("response", [_ok("본문", stop="max_tokens"), _ok("   "), {"output": {}}])
def test_bedrock_incomplete_output_is_not_retryable(response):
    with pytest.raises(pdf_r2.PageFallbackError) as info:
        pdf_r2.build_transcriber("bedrock", client=FakeBedrock(response))(b"PNG", 1)
    assert info.value.retryable is False


def test_openai_without_key_fails_without_calling(monkeypatch):
    monkeypatch.setattr(pdf_r2.httpx, "post", lambda *a, **k: pytest.fail("호출하면 안 됨"))
    with pytest.raises(pdf_r2.PageFallbackError):
        pdf_r2.build_transcriber("openai", openai_key="")(b"PNG", 1)


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        pdf_r2.build_transcriber("gemini")


class SequenceBedrock(FakeBedrock):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_bedrock_retries_once_when_output_hits_token_limit():
    client = SequenceBedrock([_ok("본문" + " " * 50, stop="max_tokens"), _ok("# 제5조\n본문")])
    assert pdf_r2.build_transcriber("bedrock", client=client)(b"PNG", 2) == "# 제5조\n본문"
    assert len(client.calls) == 2


def test_bedrock_gives_up_after_repeated_token_limit():
    client = SequenceBedrock([_ok("x", stop="max_tokens")] * 3)
    with pytest.raises(pdf_r2.PageFallbackError, match="페이지 2"):
        pdf_r2.build_transcriber("bedrock", client=client)(b"PNG", 2)
    assert len(client.calls) == pdf_r2._MAX_TOKENS_ATTEMPTS
