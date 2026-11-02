"""PDF 파서를 검수된 정답(data/kb/sections.jsonl·doc_pages.json)과 비교한다. pdftotext(poppler)가 없으면 생략."""
import re
import shutil

import pytest

from app.kb.loader import _read_json, doc_text, sections
from app.kb.pdf_parser import EXTRACTION_VERSION, PdfExtractError, extract_pages, parse_pdf, split_sections, to_chunks
from app.core.config import settings

pytestmark = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext(poppler) 없음")
_WS = re.compile(r"\s+")


@pytest.fixture(scope="module")
def parsed():
    docs = sorted({d for d, _ in sections()})
    return {d: parse_pdf(settings.data_dir / "documents" / f"{d}.pdf") for d in docs}


def test_extraction_reproduces_stored_page_texts(parsed):
    stored = _read_json("doc_pages.json")
    assert stored["extraction_version"] == EXTRACTION_VERSION
    for doc, (pages, _) in parsed.items():
        assert pages == stored["docs"][doc], doc


def test_sections_match_gold_ids_headings_positions_features(parsed):
    gold = sections()
    got = {(doc, s.section_id): s for doc, (_, secs) in parsed.items() for s in secs}
    assert set(got) == set(gold)
    for key, s in got.items():
        g = gold[key]
        assert (s.heading, s.page, s.line_start, s.page_end, s.line_end, s.feature_id, s.scope_flag) == \
            (g["heading"], g["page"], g["line_start"], g["page_end"], g["line_end"], g["feature_id"], g["scope_flag"]), key
        assert _WS.sub("", s.text) == _WS.sub("", g["text"]), key       # 줄 끝 띄어쓰기 일부는 복원 불가 (pdf_parser 문서)


def test_exact_text_rate_does_not_regress(parsed):
    gold = sections()
    exact = sum(s.text == gold[(doc, s.section_id)]["text"] for doc, (_, secs) in parsed.items() for s in secs)
    assert exact >= 114


def test_chunks_carry_gold_offsets_and_uuid_rule(parsed):
    gold = sections()
    pages, secs = parsed["CTR-A-001-v1.0"]
    for c in to_chunks("CTR-A-001-v1.0", pages, secs):
        g = gold[("CTR-A-001-v1.0", c["section_id"])]
        assert (c["chunk_id"], c["start_offset"], c["end_offset"]) == (g["chunk_id"], g["start_offset"], g["end_offset"])
        assert _WS.sub("", doc_text("CTR-A-001-v1.0")[c["start_offset"]:c["end_offset"]]) == _WS.sub("", c["text"])
    real = to_chunks("33333333-3333-4333-8333-333333333333", pages, secs)
    assert len({c["chunk_id"] for c in real}) == len(secs)


def test_duplicate_section_ids_and_textless_pdf_fail():
    page = " 제1조 목적                         C-01\n본문\n 제2조 목적                         C-01\n본문2\n"
    with pytest.raises(ValueError, match="겹침"):
        to_chunks("doc", [page], split_sections([page]))
    with pytest.raises(PdfExtractError):
        extract_pages(b"not a pdf")
