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


@pytest.fixture(autouse=True)
def _no_embedding_api(monkeypatch):
    """.env에 임베딩을 켜 두어도 테스트는 실제 임베딩 API를 부르지 않는다 (어휘 검색 고정).
    하이브리드 경로는 tests/test_retrieval_hybrid.py가 fake 임베딩으로 따로 켠다."""
    from app.tools import doc_tools
    monkeypatch.setattr(settings, "embed_provider", "none")
    doc_tools.vector_backend.cache_clear()
    yield
    doc_tools.vector_backend.cache_clear()
