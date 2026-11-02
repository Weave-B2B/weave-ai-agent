"""PDF → 페이지 텍스트 → 조항(청크). extraction_version = pdftotext-layout-v1.

추출은 poppler `pdftotext -layout` 그대로다 (data/kb/doc_pages.json과 같은 결과). 위치(page·line·offset)는
이 페이지 텍스트 기준이며, 페이지를 \\f로 이은 문자열이 offset 기준이다 (VDB 설계 v0.3 §7).

분할 규칙 (합성 계약 문서 양식):
- 조항 머리줄: 왼쪽 제목 + 오른쪽 끝 조항 ID.  예) `제1조 계약 당사자 및 목적 ...  C-01`, `01. 수행 범위 ...  S-02`
- 기능명세서 표: 한 행 = 기능 하나. 1열 `F-01`/`A-F01`, 2열 `포함`/`(IN)`, 3열 `[A-F01][IN] 기능명: 설명`
- 본문은 다음 머리줄 전까지. 페이지 머리·꼬리말, 표 머리글, 표 제목은 본문이 아니다
- 문서 끝 안내문(`※ 데이터셋의 승인·계약 정보는 가상…`)부터는 어느 조항의 본문도 아니다
- 줄바꿈은 낱말 중간에서도 일어나므로 줄을 이을 때 공백을 넣지 않는다. 앞줄이 문장부호(. : / ,)로 끝나면 한 칸 띄운다
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

EXTRACTION_VERSION = "pdftotext-layout-v1"


class PdfExtractError(RuntimeError):
    """추출 실패 (failedStage=EXTRACT)."""


def extract_pages(pdf: Path | bytes) -> list[str]:
    """pdftotext -layout. 마지막 \\f 뒤의 빈 조각은 페이지가 아니다."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise PdfExtractError("pdftotext(poppler) 없음 — brew install poppler / apt install poppler-utils")
    data = pdf if isinstance(pdf, bytes) else Path(pdf).read_bytes()
    run = subprocess.run([exe, "-layout", "-enc", "UTF-8", "-", "-"], input=data, capture_output=True, timeout=120)
    if run.returncode != 0:
        raise PdfExtractError(f"pdftotext 실패 (exit {run.returncode}): {run.stderr.decode('utf-8', 'replace')[:200]}")
    pages = run.stdout.decode("utf-8").split("\f")
    if pages and pages[-1] == "":
        pages.pop()
    if not any(p.strip() for p in pages):
        raise PdfExtractError("텍스트 없음 — 스캔본(이미지 PDF)은 OCR이 필요함")
    return pages


_HEADING = re.compile(r"^\s*(?P<heading>(?:제\d+조|\d{2}\.)\s.*?\S)\s{2,}(?P<sid>[A-Z]{1,3}-\d{2})\s*$")
_FSP_ROW = re.compile(r"^\s*(?P<sid>F-\d{2})\s{2,}(?:포함|제외)\s{2,}(?P<body>\S.*)$")
_FSP_SUB = re.compile(r"^\s*(?P<fid>[A-Z]-F\d{2})\s{2,}\((?:IN|OUT)\)\s*(?P<body>.*)$")
_FSP_TAG = re.compile(r"^\[(?P<fid>[A-Z]-F\d{2})\]\[(?P<flag>IN|OUT)\]\s*(?P<name>[^:]+?)\s*:")
_NOISE = (
    re.compile(r"^AI 모델 실험용 문서 / 합성 데이터"),               # 페이지 머리말
    re.compile(r"^\S+-v\d+\.\d+ \| v\d+\.\d+ \| .*\s\d+$"),          # 페이지 꼬리말
    re.compile(r"^섹션 / 기능 ID\s{2,}"),                            # 기능 표 머리글
    re.compile(r"^번호\s{2,}"),                                      # 견적 표 머리글
    re.compile(r"^기능 요구사항 목록"),                              # 기능 표 제목
)


_END = re.compile(r"^※ 데이터셋의 승인·계약 정보는 가상")
_SPACE_AFTER = (".", ":", "/", ",")


@dataclass
class Section:
    section_id: str
    heading: str
    page: int
    line_start: int
    page_end: int
    line_end: int
    parts: list[str] = field(default_factory=list)
    feature_id: str | None = None
    scope_flag: str | None = None

    @property
    def text(self) -> str:
        out = ""
        for part in self.parts:
            out += (" " if out.endswith(_SPACE_AFTER) else "") + part
        return out

    def add(self, page: int, line: int, part: str) -> None:
        if not part:
            return
        if not self.parts:
            self.page, self.line_start = page, line
        self.parts.append(part)
        self.page_end, self.line_end = page, line


def _noise(line: str) -> bool:
    s = line.strip()
    return any(p.search(s) for p in _NOISE)


def _cells(line: str) -> str:
    """표 행은 칸을 이어 붙이고 앞의 행 번호 칸은 버린다. 일반 문장 줄은 앞뒤 공백만 걷어낸다."""
    cells = re.split(r"\s{2,}", line.strip())
    if len(cells) > 1 and cells[0].isdigit():
        cells = cells[1:]
    return "".join(cells)


def split_sections(pages: list[str]) -> list[Section]:
    out: list[Section] = []
    cur: Section | None = None
    col = None                                           # 기능 표 3열 시작 위치
    for pno, page in enumerate(pages, 1):
        for lno, line in enumerate(page.split("\n"), 1):
            if not line.strip() or _noise(line):
                continue
            if _END.match(line.strip()):
                cur = None
                continue
            if m := _HEADING.match(line):
                cur, col = Section(m["sid"], m["heading"], pno, lno, pno, lno), None
                out.append(cur)
                continue
            if m := _FSP_ROW.match(line):
                body = m["body"]
                cur, col = Section(m["sid"], "", pno, lno, pno, lno), m.start("body")
                if t := _FSP_TAG.match(body):
                    cur.heading, cur.feature_id, cur.scope_flag = t["name"], t["fid"], t["flag"]
                out.append(cur)
                cur.add(pno, lno, body.strip())
                continue
            if cur is None:
                continue                                 # 표지·문서 정보 표
            if col is not None:
                sub = _FSP_SUB.match(line)
                cur.add(pno, lno, (sub["body"] if sub else line[col:] if len(line) > col else line).strip())
            else:
                cur.add(pno, lno, _cells(line))
    return out


def parse_pdf(pdf: Path | bytes) -> tuple[list[str], list[Section]]:
    pages = extract_pages(pdf)
    return pages, split_sections(pages)


def to_chunks(document_version_id: str, pages: list[str], secs: list[Section],
              extraction_version: str = EXTRACTION_VERSION) -> list[dict]:
    """조항 → 청크 dict (loader.sections()와 같은 키). chunk_id는 CN과 같은 UUID5 규칙, offset은 \\f 연결 텍스트 기준."""
    from app.kb.loader import _offsets, chunk_uuid
    ids = [s.section_id for s in secs]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"문서 안에서 조항 ID가 겹침: {dup}")       # failedStage=CHUNK
    out = []
    for s in secs:
        d = {"doc_id": document_version_id, "section_id": s.section_id, "heading": s.heading, "text": s.text,
             "page": s.page, "line_start": s.line_start, "page_end": s.page_end, "line_end": s.line_end,
             "feature_id": s.feature_id, "scope_flag": s.scope_flag, "extraction_version": extraction_version}
        d["start_offset"], d["end_offset"] = _offsets(pages, d)
        d["chunk_id"] = chunk_uuid(document_version_id, s.section_id, extraction_version)
        out.append(d)
    return out
