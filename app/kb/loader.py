"""지식베이스 로더. 검색이 읽을 수 있는 파일은 ALLOWLIST 뿐이다.

eval/dataset(정답·chain·noisy 등)은 절대 읽지 않는다 (manifest.never_index).
"""
import json
import os
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
    """CN과 같은 규칙 (VDB 설계 v0.3 §3): uuid5(CHUNK_NS, "{documentVersionId}:{sectionId}:{extractionVersion}").
    실제 문서 버전 UUID는 소문자·하이픈 표준형으로 맞춘다. 샘플 문서 이름(UUID 아님)은 그대로 쓴다."""
    if ":" in section_id:
        raise ValueError(f"section_id에 ':'를 쓸 수 없음: {section_id}")
    try:
        doc_id = str(uuid.UUID(doc_id))
    except ValueError:
        pass
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


def lab_kb_path() -> Path:
    """프로토타입(--lab)에서 올린 테스트 문서·기준선. 원본 data/kb는 고치지 않는다 (단가표 로컬 버전과 같은 방식)."""
    return settings.data_dir.parent / ".prototype-kb" / "kb.json"


def _lab_kb() -> dict:
    if os.environ.get("PROTOTYPE_LAB") != "1" or not lab_kb_path().is_file():
        return {}
    return json.loads(lab_kb_path().read_text(encoding="utf-8"))


def clear_caches() -> None:
    for f in (sections, documents, baselines):
        f.cache_clear()


def doc_text(doc_id: str) -> str:
    """offset 기준 텍스트: 추출 페이지를 \\f로 이은 것."""
    pages = _read_json("doc_pages.json")["docs"].get(doc_id) or _lab_kb().get("pages", {}).get(doc_id, [])
    return "\f".join(pages)


@lru_cache
def sections() -> dict[tuple[str, str], dict]:
    pages = _read_json("doc_pages.json")["docs"]
    out = {}
    for s in _read_jsonl("sections.jsonl"):
        start, end = _offsets(pages.get(s["doc_id"], []), s)
        out[(s["doc_id"], s["section_id"])] = {
            **s, "chunk_id": chunk_uuid(s["doc_id"], s["section_id"], s["extraction_version"]),
            "start_offset": start, "end_offset": end}
    for s in _lab_kb().get("sections", []):                # 색인 파이프라인이 만든 청크 (chunk_id·offset 포함)
        out.setdefault((s["doc_id"], s["section_id"]), s)
    return out


@lru_cache
def documents() -> dict[str, dict]:
    out = {d["doc_id"]: d for d in _read_jsonl("documents.jsonl")}
    for d in _lab_kb().get("documents", []):
        out.setdefault(d["doc_id"], d)
    return out


@lru_cache
def baselines() -> dict[str, dict]:
    out = dict(_read_json("baselines.json")["baselines"])
    for k, b in _lab_kb().get("baselines", {}).items():
        out.setdefault(k, b)
    return out


def superseded_by(baseline_id: str) -> Optional[str]:
    """이 기준선을 대체한 최신 기준선 ID. 현재 기준선이면 None. 상태의 원본은 백엔드 Baseline(ACTIVE/SUPERSEDED)이고,
    연동 전에는 baselines.json의 supersedes로 대신한다."""
    newer = {b["supersedes"]: k for k, b in baselines().items() if b.get("supersedes")}
    latest, seen = None, {baseline_id}
    while baseline_id in newer and newer[baseline_id] not in seen:
        latest = baseline_id = newer[baseline_id]
        seen.add(latest)
    return latest


def rate_cards() -> dict[str, dict]:
    # Avoid stale rates after a supplied file is replaced.
    path = settings.rate_card_file or settings.data_dir / "kb" / "rate_cards.json"
    cards = load_rate_cards(path)
    # Local prototype edits are separate, immutable versions; never overwrite source files.
    if os.environ.get("PROTOTYPE_LAB") == "1":
        directory = settings.data_dir.parent / ".prototype-rate-cards"
        if directory.is_dir():
            for local_file in sorted(directory.glob("*.json")):
                local_cards = load_rate_cards(local_file)
                if cards.keys() & local_cards.keys():
                    raise ValueError("duplicate local rate card ID")
                cards.update(local_cards)
    return cards


@lru_cache
def schedules() -> dict[str, dict]:
    return _read_json("schedules.json")["projects"]


def retrieval_sources() -> list[Path]:
    """색인에 쓸 수 있는 실제 파일 목록 (테스트가 eval/ 포함 여부를 검사한다)."""
    out: list[Path] = []
    for pat in RETRIEVAL_ALLOWLIST:
        out += sorted(settings.data_dir.glob(pat))
    return out
