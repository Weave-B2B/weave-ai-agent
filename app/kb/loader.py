"""지식베이스 로더. 검색이 읽을 수 있는 파일은 ALLOWLIST 뿐이다.

eval/dataset(정답·chain·noisy 등)은 절대 읽지 않는다 (manifest.never_index).
"""
import json
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.kb.rates import load_rate_cards

# 검색 허용: kb/sections.jsonl · kb/doc_pages.json · PDF 원본 (README '분할·검색')
RETRIEVAL_ALLOWLIST = ("kb/sections.jsonl", "kb/doc_pages.json", "documents/*.pdf")


def _read_json(name: str):
    return json.loads((settings.data_dir / "kb" / name).read_text(encoding="utf-8"))


def _read_jsonl(name: str) -> list[dict]:
    p = settings.data_dir / "kb" / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


@lru_cache
def sections() -> dict[tuple[str, str], dict]:
    return {(s["doc_id"], s["section_id"]): s for s in _read_jsonl("sections.jsonl")}


@lru_cache
def documents() -> dict[str, dict]:
    return {d["doc_id"]: d for d in _read_jsonl("documents.jsonl")}


@lru_cache
def baselines() -> dict[str, dict]:
    return _read_json("baselines.json")["baselines"]


def rate_cards() -> dict[str, dict]:
    # Avoid stale rates after a supplied file is replaced.
    path = settings.rate_card_file or settings.data_dir / "kb" / "rate_cards.json"
    return load_rate_cards(path)


@lru_cache
def schedules() -> dict[str, dict]:
    return _read_json("schedules.json")["projects"]


def retrieval_sources() -> list[Path]:
    """색인에 쓸 수 있는 실제 파일 목록 (테스트가 eval/ 포함 여부를 검사한다)."""
    out: list[Path] = []
    for pat in RETRIEVAL_ALLOWLIST:
        out += sorted(settings.data_dir.glob(pat))
    return out
