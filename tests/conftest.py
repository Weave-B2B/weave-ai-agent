import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402

EVAL = settings.eval_dir


@pytest.fixture(scope="session", autouse=True)
def _record_calculation_fixtures():
    if os.environ.get("WEAVE_RECORD_CALCULATIONS") == "1":
        from support.calculation_recorder import install
        with pytest.MonkeyPatch.context() as patch:
            install(patch)
            yield
    else:
        yield


def jl(path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def split_rows(agent: str, kind: str) -> list[dict]:
    return [r for s in ("train", "validation", "test") for r in jl(EVAL / agent / s / f"{kind}.jsonl")]


@pytest.fixture(scope="session")
def chain() -> list[dict]:
    return [r for s in ("train", "validation", "test") for r in jl(EVAL / "chain" / f"{s}.jsonl")]


@pytest.fixture(autouse=True)
def _no_embedding_api(monkeypatch):
    """.env 설정과 무관하게 테스트는 실제 LLM·임베딩 API를 부르지 않는다 (fixture 에이전트·어휘 검색 고정).
    하이브리드 경로는 tests/test_retrieval_hybrid.py가 fake 임베딩으로 따로 켠다."""
    from app.tools import doc_tools
    monkeypatch.setattr(settings, "embed_provider", "none")
    monkeypatch.setattr(settings, "vector_store", "local")     # .env의 VECTOR_STORE=pgvector가 테스트에 섞이지 않게
    monkeypatch.setattr(settings, "agent_mode", "fixture")     # .env의 AGENT_MODE=llm이 실제 LLM을 부르지 않게
    monkeypatch.setattr(settings, "calculation_mode", "fixture")
    monkeypatch.setattr(settings, "result_transport", "http")
    monkeypatch.setattr(settings, "pdf_parser", "legacy")      # 색인 테스트는 기존 파서 기준. r2 경로는 각 테스트가 켠다  # .env의 RESULT_TRANSPORT=sqs(로컬 기본)가 결과를 큐로 보내지 않게
    doc_tools.vector_backend.cache_clear()
    yield
    doc_tools.vector_backend.cache_clear()
