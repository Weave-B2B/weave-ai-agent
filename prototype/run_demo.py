"""Run the local prototype with safe fake defaults or explicit Gemini API mode."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass
class RunConfig:
    mode: str
    model: str
    rate_file: Path
    provider: str = "fake"
    api_key: str = field(default="", repr=False)
    tuning: dict[str, str] = field(default_factory=dict)


def configuration(args, environ) -> RunConfig:
    """Load provider credentials without exposing or constructing an HTTP client."""
    mode = "gemini" if args.mode == "gemini" else ("openai" if args.mode in ("api", "openai") else ("ollama" if args.mode == "ollama" else "fake"))
    file_values = dotenv_values(ROOT / ".env") if (ROOT / ".env").is_file() else {}
    gemini_key = (environ.get("GEMINI_API_KEY") or file_values.get("GEMINI_API_KEY") or "").strip()
    llm_key = (environ.get("LLM_API_KEY") or file_values.get("LLM_API_KEY") or "").strip()
    openai_model = (environ.get("LLM_MODEL") or file_values.get("LLM_MODEL") or "gpt-4.1-mini").strip()
    ollama_model = (environ.get("OLLAMA_MODEL") or file_values.get("OLLAMA_MODEL") or "gpt-oss:20b").strip()
    path = Path(args.rate_card_file or environ.get("RATE_CARD_FILE") or ROOT / "data/kb/rate_cards.json").expanduser().resolve()
    provider = mode
    key = ""
    if mode == "gemini":
        key = gemini_key
        if not key:
            raise ValueError("Gemini mode requires GEMINI_API_KEY")
    elif mode == "openai":
        key = llm_key
        if not key:
            raise ValueError("OpenAI/API mode requires LLM_API_KEY")
    model = args.model or ({"gemini": GEMINI_MODEL, "openai": openai_model, "ollama": ollama_model}.get(provider, "fake-ui-demo"))
    if not model.strip() or any(char.isspace() for char in model):
        raise ValueError("model must be a nonempty model ID without whitespace")
    tuning = {}
    for name, default, lower, upper, integer in (
        ("LLM_MAX_TOKENS", "4096", 1, None, True),
        ("LLM_TEMPERATURE", "0", 0, 2, False),
        ("LLM_READ_TIMEOUT_SEC", "50", 0, None, False),
        ("LLM_CONNECT_TIMEOUT_SEC", "5", 0, None, False),
        ("OLLAMA_READ_TIMEOUT_SEC", "300", 0, None, False),
        ("OLLAMA_CONNECT_TIMEOUT_SEC", "10", 0, None, False),
        ("OLLAMA_NUM_PREDICT", "1024", 1, None, True),
    ):
        try:
            value = int(environ.get(name, default)) if integer else float(environ.get(name, default))
            valid = math.isfinite(value) and value >= lower and (upper is None or value <= upper)
            if "TIMEOUT" in name:
                valid = valid and value > 0
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"Invalid setting: {name}")
        tuning[name] = str(value)
    return RunConfig(mode=mode, model=model, rate_file=path, provider=provider, api_key=key, tuning=tuning)


def apply_environment(config: RunConfig, responses: Path | None = None) -> None:
    """Configure the shared factory for the selected provider and enable real Agent execution."""
    os.environ.update({
        "AGENT_MODE": "llm" if config.mode in ("gemini", "openai", "ollama") else "fixture",
        "AGENT2_MODE": "llm" if config.mode in ("gemini", "openai", "ollama") else "fixture",
        "LLM_PROVIDER": "local" if config.provider == "gemini" else ("openai" if config.provider == "openai" else ("ollama" if config.provider == "ollama" else "fake")),
        "LLM_MODEL": config.model,
        "LLM_BASE_URL": GEMINI_BASE_URL if config.provider == "gemini" else "",
        "OLLAMA_BASE_URL": (os.environ.get("OLLAMA_BASE_URL") or dotenv_values(ROOT / ".env").get("OLLAMA_BASE_URL") or "http://localhost:11434") if config.provider == "ollama" else os.environ.get("OLLAMA_BASE_URL", ""),
        "OLLAMA_MODEL": config.model if config.provider == "ollama" else (os.environ.get("OLLAMA_MODEL", "") or dotenv_values(ROOT / ".env").get("OLLAMA_MODEL", "")),
        "OLLAMA_KEEP_ALIVE": os.environ.get("OLLAMA_KEEP_ALIVE") or dotenv_values(ROOT / ".env").get("OLLAMA_KEEP_ALIVE") or "30m",
        "GEMINI_API_KEY": config.api_key if config.provider == "gemini" else os.environ.get("GEMINI_API_KEY", ""),
        "LLM_API_KEY": config.api_key if config.provider == "openai" else os.environ.get("LLM_API_KEY", ""),
        "LLM_LOCAL_JSON_MODE": "schema",
        "LLM_FAKE_RESPONSES": str(responses) if responses else "",
        "RATE_CARD_FILE": str(config.rate_file),
        **config.tuning,
    })


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lab", action="store_true", help="Enable local developer controls using server-side .env credentials")
    parser.add_argument("--mode", choices=("fake", "gemini", "openai", "api", "ollama"), default="fake")
    parser.add_argument("--model", help="Explicit Gemini model override; default is the pinned stable preset")
    parser.add_argument("--rate-card-file", help="Reviewed rate_cards JSON; otherwise RATE_CARD_FILE or bundled synthetic table")
    parser.add_argument("--check", action="store_true", help="Validate configuration and rate data with zero network calls; do not start a server")
    args = parser.parse_args(argv)
    if args.lab and args.host not in ("127.0.0.1", "::1", "localhost"):
        parser.error("--lab requires a loopback host")
    sys.path.insert(0, str(ROOT))
    from app.kb.rates import load_rate_cards

    try:
        config = configuration(args, os.environ)
    except ValueError as error:
        parser.exit(1, f"Configuration error: {error}\n")
    try:
        cards = load_rate_cards(config.rate_file)
    except (ValueError, OSError):
        parser.exit(1, "Rate card data unavailable or invalid; require reviewed KRW/MD JSON, VAT excluded.\n")
    print(json.dumps({"mode": config.mode, "model": config.model,
        "api_key_configured": bool(config.api_key), "rate_card_ids": sorted(cards),
        "synthetic_rate_cards": sorted(k for k, card in cards.items() if card.get("synthetic") is True),
        "check_only": args.check}, ensure_ascii=False))
    if args.check:
        return

    os.chdir(ROOT)
    with tempfile.TemporaryDirectory(prefix="weave-agent3-demo-") as folder:
        responses = None
        if config.mode == "fake":
            source = json.loads((ROOT / "examples/agent3/fake_responses.json").read_text(encoding="utf-8"))
            if not source:
                parser.exit(1, "The synthetic response fixture is empty.\n")
            responses = Path(folder) / "fake-responses.json"
            responses.write_text(json.dumps(source * 200, ensure_ascii=False), encoding="utf-8")
        apply_environment(config, responses)
        if args.lab:
            os.environ["PROTOTYPE_LAB"] = "1"
        import uvicorn

        print(f"Local prototype: http://{args.host}:{args.port}/prototype/")
        print("Offline synthetic AI responses." if config.mode == "fake" else "API mode: prototype Agent 1-3 execution uses configured provider credentials; no fake fallback.")
        print("Code calculation, PM review, and model-generated internal draft; Agent 1-3 are real in API mode." if config.mode != "fake" else "Code calculation, PM review, and Agent responses use dataset fixtures in fake mode.")
        print("Agent 1-3: real API agents." if config.mode != "fake" else "Agent 1-3: dataset fixtures.")
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.lab,
        )


if __name__ == "__main__":
    main()
