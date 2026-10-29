"""지식베이스 로더. 검색이 읽을 수 있는 파일은 ALLOWLIST 뿐이다.

eval/dataset(정답·chain·noisy 등)은 절대 읽지 않는다 (manifest.never_index).
"""
import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.kb.rates import load_rate_cards

# 검색 허용: kb/sections.jsonl · kb/doc_pages.json · PDF 원본 (README '분할·검색')
RETRIEVAL_ALLOWLIST = ("kb/sections.jsonl", "kb/doc_pages.json", "documents/*.pdf")


def _read_json(name: str):
    return json.loads((settings.data_dir / "kb" / name).read_text(encoding="utf-8"))


def _read_jsonl(name: str) -> list[dict]:
    p = settings.data_dir / "kb" / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ERD document_chunk 대응: 섹션 1개 = 청크 1행. chunk_id는 (문서·섹션·추출 버전)에서 결정적으로 만든다
CHUNK_NS = uuid.UUID("6f1d3c52-7a0e-4b8e-9d2a-2a7f0c0e5a11")


def chunk_uuid(doc_id: str, section_id: str, extraction_version: str) -> str:
    return str(uuid.uuid5(CHUNK_NS, f"{doc_id}:{section_id}:{extraction_version}"))


def _offsets(pages: list[str], s: dict) -> tuple[Optional[int], Optional[int]]:
    """줄 번호(page·line)를 문서 전체 텍스트(페이지를 \\f로 이음) 기준 문자 offset [start, end)로 바꾼다."""
    if not pages or s.get("page") is None or s.get("line_start") is None:
        return None, None
    base = [0]
    for p in pages:
        base.append(base[-1] + len(p) + 1)              # +1 = 페이지 구분 \f

    def at(page: int, line: int, end: bool) -> int:
        lines = pages[page - 1].split("\n")
        line = min(max(line, 1), len(lines))
        pos = sum(len(l) + 1 for l in lines[:line - 1])
        return base[page - 1] + pos + (len(lines[line - 1]) if end else 0)
    return at(s["page"], s["line_start"], False), at(s["page_end"] or s["page"], s["line_end"] or s["line_start"], True)


def doc_text(doc_id: str) -> str:
    """offset 기준 텍스트: 추출 페이지를 \\f로 이은 것."""
    return "\f".join(_read_json("doc_pages.json")["docs"].get(doc_id, []))


@lru_cache
def sections() -> dict[tuple[str, str], dict]:
    pages = _read_json("doc_pages.json")["docs"]
    out = {}
    for s in _read_jsonl("sections.jsonl"):
        start, end = _offsets(pages.get(s["doc_id"], []), s)
        out[(s["doc_id"], s["section_id"])] = {
            **s, "chunk_id": chunk_uuid(s["doc_id"], s["section_id"], s["extraction_version"]),
            "start_offset": start, "end_offset": end}
    return out


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
