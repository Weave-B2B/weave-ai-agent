"""Offline configuration selection, secret handling and zero-network preflight."""

import argparse
import json
from pathlib import Path
import socket

import httpx
import pytest

from prototype import run_demo


def args(**changes):
    return argparse.Namespace(mode="fake", model=None, rate_card_file=None, **changes)


def test_fake_default_and_explicit_gemini_presets_ignore_stale_provider_environment(monkeypatch):
    environment = {"LLM_PROVIDER": "bedrock", "LLM_MODEL": "old-model", "LLM_BASE_URL": "https://old.invalid", "GEMINI_API_KEY": "mock-secret"}
    fake = run_demo.configuration(args(), environment)
    assert fake.mode == "fake" and fake.api_key == "" and fake.model == "fake-ui-demo"
    selected = args()
    selected.mode = "gemini"
    gemini = run_demo.configuration(selected, environment)
    assert gemini.model == "gemini-3.5-flash-lite" and gemini.api_key == "mock-secret"
    assert "mock-secret" not in repr(gemini)
    for name in ("AGENT_MODE", "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "LLM_LOCAL_JSON_MODE", "LLM_FAKE_RESPONSES", "RATE_CARD_FILE", "LLM_MAX_TOKENS"):
        monkeypatch.setenv(name, "stale")
    run_demo.apply_environment(gemini)
    import os
    assert os.environ["LLM_PROVIDER"] == "local"
    assert os.environ["AGENT_MODE"] == "fixture"
    assert os.environ["LLM_BASE_URL"] == run_demo.GEMINI_BASE_URL
    assert os.environ["LLM_FAKE_RESPONSES"] == ""
    assert os.environ["LLM_API_KEY"] == "mock-secret"
    assert os.environ["LLM_MAX_TOKENS"] == "4096"


def test_model_and_rate_override_precedence(tmp_path):
    selected = args()
    selected.mode, selected.model, selected.rate_card_file = "api", "gemini-explicit", str(tmp_path / "explicit.json")
    config = run_demo.configuration(selected, {"LLM_API_KEY": "mock-fallback", "RATE_CARD_FILE": "other.json"})
    assert config.mode == "gemini" and config.model == "gemini-explicit"
    assert config.rate_file == tmp_path / "explicit.json"
    assert config.api_key == "mock-fallback"
    selected.rate_card_file = None
    assert run_demo.configuration(selected, {"GEMINI_API_KEY": "mock", "RATE_CARD_FILE": str(tmp_path / "env.json")}).rate_file == tmp_path / "env.json"


def test_check_never_constructs_http_client_starts_server_reads_dotenv_or_reveals_key(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight attempted network/server construction")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        assert path.name != ".env", "preflight read original .env"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setenv("GEMINI_API_KEY", "mock-private-key")
    run_demo.main(["--mode", "gemini", "--check"])
    output = capsys.readouterr().out
    assert "mock-private-key" not in output
    assert json.loads(output)["api_key_configured"] is True
    assert json.loads(output)["check_only"] is True


def test_missing_key_and_invalid_settings_fail_without_values(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        run_demo.main(["--mode", "gemini", "--check"])
    assert error.value.code == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("LLM_TEMPERATURE", "private-invalid-value")
    with pytest.raises(SystemExit):
        run_demo.main(["--check"])
    assert "private-invalid-value" not in capsys.readouterr().err


def test_invalid_rate_file_fails_check(tmp_path, capsys):
    path = tmp_path / "rates.json"
    path.write_text('{"rate_cards": {}}')
    with pytest.raises(SystemExit) as error:
        run_demo.main(["--check", "--rate-card-file", str(path)])
    assert error.value.code == 1
    assert "Rate card data unavailable or invalid" in capsys.readouterr().err
