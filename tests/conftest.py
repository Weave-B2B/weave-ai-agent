import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402

EVAL = settings.eval_dir


def jl(path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def split_rows(agent: str, kind: str) -> list[dict]:
    return [r for s in ("train", "validation", "test") for r in jl(EVAL / agent / s / f"{kind}.jsonl")]


@pytest.fixture(scope="session")
def chain() -> list[dict]:
    return [r for s in ("train", "validation", "test") for r in jl(EVAL / "chain" / f"{s}.jsonl")]
