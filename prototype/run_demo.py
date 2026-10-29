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

ROOT = Path(__file__).resolve().parents[1]
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass
class RunConfig:
    mode: str
    model: str
    rate_file: Path
    api_key: str = field(default="", repr=False)
    tuning: dict[str, str] = field(default_factory=dict)


def configuration(args, environ) -> RunConfig:
    """Select explicit presets. Never load .env or construct an HTTP client here."""
    mode = "gemini" if args.mode == "api" else args.mode
    path = Path(args.rate_card_file or environ.get("RATE_CARD_FILE") or ROOT / "data/kb/rate_cards.json").expanduser().resolve()
    key = ((environ.get("GEMINI_API_KEY") or "").strip() or (environ.get("LLM_API_KEY") or "").strip()) if mode == "gemini" else ""
    if mode == "gemini" and not key:
        raise ValueError("Gemini mode requires server GEMINI_API_KEY or LLM_API_KEY")
    model = (args.model or GEMINI_MODEL) if mode == "gemini" else "fake-ui-demo"
    if not model.strip() or any(char.isspace() for char in model):
        raise ValueError("model must be a nonempty model ID without whitespace")
    tuning = {}
    for name, default, lower, upper, integer in (
        ("LLM_MAX_TOKENS", "4096", 1, None, True),
        ("LLM_TEMPERATURE", "0", 0, 2, False),
        ("LLM_READ_TIMEOUT_SEC", "50", 0, None, False),
        ("LLM_CONNECT_TIMEOUT_SEC", "5", 0, None, False),
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
    return RunConfig(mode, model, path, key, tuning)


def apply_environment(config: RunConfig, responses: Path | None = None) -> None:
    """Set the existing factory's inputs; A1/A2 remain fixtures in both modes."""
    os.environ.update({
        "AGENT_MODE": "fixture",
        "LLM_PROVIDER": "local" if config.mode == "gemini" else "fake",
        "LLM_MODEL": config.model,
        "LLM_BASE_URL": GEMINI_BASE_URL if config.mode == "gemini" else "",
        "LLM_API_KEY": config.api_key,
        "LLM_LOCAL_JSON_MODE": "schema",
        "LLM_FAKE_RESPONSES": str(responses) if responses else "",
        "RATE_CARD_FILE": str(config.rate_file),
        **config.tuning,
    })


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mode", choices=("fake", "gemini", "api"), default="fake")
    parser.add_argument("--model", help="Explicit Gemini model override; default is the pinned stable preset")
    parser.add_argument("--rate-card-file", help="Reviewed rate_cards JSON; otherwise RATE_CARD_FILE or bundled synthetic table")
    parser.add_argument("--check", action="store_true", help="Validate configuration and rate data with zero network calls; do not start a server")
    args = parser.parse_args(argv)
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
        import uvicorn

        print(f"Local prototype: http://{args.host}:{args.port}/prototype/")
        print("Offline synthetic AI responses." if config.mode == "fake" else "Gemini API mode: impact estimation sends selected requirements to Google; no fake fallback.")
        print("Existing code calculation, PM review, and deterministic internal draft; A1/A2 fixtures.")
        uvicorn.run("app.main:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
