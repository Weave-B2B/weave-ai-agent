import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.agents.base import AgentFailure
from app.core.config import Settings
from app.llm.bedrock import BedrockLlm
from app.llm.factory import LlmConfigError, build_llm
from app.llm.fake import FakeLlm
from app.llm.local import LocalLlm


class ReplyModel(BaseModel):
    answer: str


def _settings(**values) -> Settings:
    """Create isolated settings without reading the user's environment."""
    return Settings(_env_file=None, **values)


def test_factory_selects_each_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.bedrock.boto3.client", lambda *args, **kwargs: object())

    assert isinstance(build_llm(_settings(llm_provider="fake")), FakeLlm)
    assert isinstance(
        build_llm(
            _settings(
                llm_provider="local",
                llm_model="local-model",
                llm_base_url="http://localhost:8000/v1",
            )
        ),
        LocalLlm,
    )
    assert isinstance(
        build_llm(
            _settings(
                llm_provider="bedrock",
                llm_model="bedrock-model",
                aws_region="us-west-2",
            )
        ),
        BedrockLlm,
    )


def test_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_llm(_settings(llm_provider="unknown"))


def test_invalid_local_json_mode_names_only_the_setting() -> None:
    config = _settings(
        llm_provider="local",
        llm_base_url="http://local.test",
        llm_model="model",
        llm_local_json_mode="secret-invalid-value",
    )

    with pytest.raises(LlmConfigError) as caught:
        build_llm(config)

    assert str(caught.value) == "Invalid setting: LLM_LOCAL_JSON_MODE"


@pytest.mark.parametrize(
    ("provider", "overrides", "missing_name", "secret_value"),
    [
        ("local", {"llm_base_url": "", "llm_model": "TOP_SECRET_MODEL"}, "LLM_BASE_URL", "TOP_SECRET_MODEL"),
        ("local", {"llm_base_url": "TOP_SECRET_URL", "llm_model": ""}, "LLM_MODEL", "TOP_SECRET_URL"),
        ("bedrock", {"llm_model": "", "aws_region": "TOP_SECRET_REGION"}, "LLM_MODEL", "TOP_SECRET_REGION"),
        ("bedrock", {"llm_model": "TOP_SECRET_MODEL", "aws_region": ""}, "AWS_REGION", "TOP_SECRET_MODEL"),
    ],
)
def test_missing_provider_configuration_fails_without_fallback(
    provider: str,
    overrides: dict[str, str],
    missing_name: str,
    secret_value: str,
) -> None:
    config = _settings(llm_provider=provider, **overrides)

    with pytest.raises(LlmConfigError) as caught:
        build_llm(config)

    assert missing_name in str(caught.value)
    assert secret_value not in str(caught.value)


def test_fake_responses_file_loads_payloads_and_error_shortcuts(tmp_path) -> None:
    response_file = tmp_path / "fake-responses.json"
    response_file.write_text(
        json.dumps([{"answer": "first"}, {"error": "timeout"}, "```json\n{\"answer\":\"last\"}\n```"]),
        encoding="utf-8",
    )
    client = build_llm(
        _settings(llm_provider="fake", llm_fake_responses=str(response_file))
    )

    first = client.complete_structured(
        system="system",
        user="first",
        schema=ReplyModel,
    )
    assert first.parsed.answer == "first"

    with pytest.raises(AgentFailure) as caught:
        client.complete_structured(
            system="system",
            user="second",
            schema=ReplyModel,
        )
    assert caught.value.error.code == "MODEL_TIMEOUT"

    last = client.complete_structured(
        system="system",
        user="last",
        schema=ReplyModel,
    )
    assert last.parsed.answer == "last"


def test_fake_response_shortage_and_malformed_file_are_explicit(tmp_path) -> None:
    with pytest.raises(LlmConfigError, match="Could not load LLM_FAKE_RESPONSES"):
        build_llm(
            _settings(
                llm_provider="fake",
                llm_fake_responses=str(tmp_path / "does-not-exist.json"),
            )
        )

    client = build_llm(_settings(llm_provider="fake"))
    with pytest.raises(AgentFailure) as caught:
        client.complete_structured(system="system", user="user", schema=ReplyModel)
    assert caught.value.error.code == "INTERNAL_ERROR"
    assert caught.value.error.message == "fake 응답 부족"
    assert caught.value.error.retryable is False


@pytest.mark.parametrize(
    ("error_name", "expected_code", "expected_retryable"),
    [
        ("timeout", "MODEL_TIMEOUT", True),
        ("throttle", "MODEL_TIMEOUT", True),
        ("auth", "INTERNAL_ERROR", False),
    ],
)
def test_fake_error_shortcuts_map_to_shared_agent_errors(
    tmp_path,
    error_name: str,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    response_file = tmp_path / "fake-error.json"
    response_file.write_text(json.dumps([{"error": error_name}]), encoding="utf-8")
    client = build_llm(
        _settings(llm_provider="fake", llm_fake_responses=str(response_file))
    )

    with pytest.raises(AgentFailure) as caught:
        client.complete_structured(system="system", user="user", schema=ReplyModel)

    assert caught.value.error.code == expected_code
    assert caught.value.error.retryable is expected_retryable


def test_fixture_startup_ignores_invalid_llm_provider(monkeypatch) -> None:
    import app.main as main

    settings = _settings(agent_mode="fixture", llm_provider="not-a-provider")
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(
        main,
        "build_llm",
        lambda _: pytest.fail("fixture startup must not build an LLM client"),
    )

    with TestClient(main.app):
        pass


def test_llm_startup_runs_configuration_validation(monkeypatch) -> None:
    import app.main as main

    settings = _settings(agent_mode="llm", llm_provider="local", llm_base_url="", llm_model="")
    monkeypatch.setattr(main, "settings", settings)

    with pytest.raises(LlmConfigError, match="LLM_BASE_URL"):
        with TestClient(main.app):
            pass


def test_client_public_module_reexports_factory_and_providers() -> None:
    from app.llm.client import FakeLlm as PublicFakeLlm
    from app.llm.client import LlmClient, build_llm as public_build_llm

    assert PublicFakeLlm is FakeLlm
    assert callable(public_build_llm)
    assert LlmClient is not None
