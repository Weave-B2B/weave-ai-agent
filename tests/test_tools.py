import pytest
from conftest import jl, split_rows
from app.kb.loader import RETRIEVAL_ALLOWLIST, retrieval_sources
from app.core.config import settings
from app.tools.calc_cost import calc_cost
from app.tools.doc_tools import get_section, search_docs, verify_quote
from app.tools.errors import ToolInputError
from app.tools.get_schedule import get_schedule


def test_every_gold_quote_verifies_and_locates():
    n = 0
    for a2 in split_rows("agent2", "gold"):
        for a in a2["assessments"]:
            for e in a["evidence"]:
                assert verify_quote(e["quote"], e["doc_id"], e["section_id"])["verified"], e
                sec = get_section(e["doc_id"], e["section_id"])
                assert (sec["page"], sec["line_start"]) == (e["page"], e["line_start"]), e
                n += 1
    assert n >= 50


def test_verify_quote_rejects_altered_text():
    assert not verify_quote("구매 후기는 계약 범위에 포함된다", "FSP-A-001-v1.0", "F-11")["verified"]
    assert not verify_quote("아무 문장", "NO-DOC", "X-1")["verified"]


def test_search_is_isolated_by_baseline():
    hits = search_docs("관리자 배송 조회 링크", "P-COM-102:BL-1", top_k=10)
    assert hits and all(h["doc_id"].split("-")[1] == "B" for h in hits)
    bl1 = {h["doc_id"] for h in search_docs("배송사 조회 링크", "P-COM-101:BL-1", top_k=10)}
    assert "CO-A-001-v1.0" not in bl1              # 변경합의서는 BL-1(2026-09-01)에 없다
    assert any(h["doc_id"] == "CO-A-001-v1.0" for h in search_docs("배송사 조회 링크", "P-COM-101:BL-2", top_k=10))
    with pytest.raises(ToolInputError):
        search_docs("x", "P-COM-999:BL-9")


def test_calc_cost_reproduces_every_gold_total():
    n = 0
    for g in jl(settings.eval_dir / "agent3" / "gold.jsonl"):
        for e in g["estimates"]:
            if e["totals"] is None:
                continue
            assert calc_cost(e["lines"], e["rate_card_id"]) == e["totals"], g["context"]["case_id"]
            n += 1
    assert n > 10


def test_calc_cost_errors():
    with pytest.raises(ToolInputError): calc_cost([{"role": "FE", "md": -1}], "RC-SYNTH-2026H2-v1")
    with pytest.raises(ToolInputError): calc_cost([{"role": "FE", "md": 0.3}], "RC-SYNTH-2026H2-v1")
    with pytest.raises(ToolInputError): calc_cost([{"role": "XX", "md": 1}], "RC-SYNTH-2026H2-v1")
    with pytest.raises(ToolInputError): calc_cost([{"role": "FE", "md": 1}], "NOPE")
    r = calc_cost([{"role": "QA", "md": 1}], "RC-SYNTH-2026H2-noQA")   # 단가 누락은 오류가 아니라 NEEDS_INPUT
    assert r["status"] == "NEEDS_INPUT" and r["totals"] is None and r["missing"] == ["rate:QA"]
    assert calc_cost([{"role": "QA", "md": 0}], "RC-SYNTH-2026H2-noQA")["cost"] == 0   # 0MD는 단가 불필요


def test_schedule_respects_as_of():
    before = get_schedule("P-COM-101", "2026-10-04")["contract_end"]
    after = get_schedule("P-COM-101", "2026-10-05")["contract_end"]
    assert (before, after) == ("2026-11-23", "2026-11-24")


def test_retrieval_only_reads_allowlist():
    paths = retrieval_sources()
    assert paths and not any("eval" in p.parts for p in paths)
    assert any(p.name == "sections.jsonl" for p in paths) and any(p.suffix == ".pdf" for p in paths)
    assert RETRIEVAL_ALLOWLIST == ("kb/sections.jsonl", "kb/doc_pages.json", "documents/*.pdf")
