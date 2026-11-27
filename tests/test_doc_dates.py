"""#92 효력일·서명일·기간 추출과 Agent2 기준선 문맥."""
import app.agents.agent2.agent  # noqa: F401  (Agent2 import 확인)
from app.indexing.doc_dates import effect_status, extract_dates
from app.tools.doc_review import open_document
from app.tools.doc_tools import list_baseline_docs


def test_effective_and_signed_dates():
    d = extract_dates([{"section_id": "CO-03", "text": "본 변경합의서의 효력일은 2026-10-05로 한다."},
                       {"section_id": "SIG", "text": "서명일: 2026년 10월 3일"}])
    assert d["effective_date"] == "2026-10-05" and d["signed_date"] == "2026-10-03"
    assert {e["section"] for e in d["evidence"]} == {"CO-03", "SIG"}


def test_period_range():
    d = extract_dates([{"text": "수행기간은 2026-09-01부터 2026-11-23까지로 한다."}])
    assert (d["period_start"], d["period_end"]) == ("2026-09-01", "2026-11-23")
    d = extract_dates([{"text": "계약기간: 2026.09.14 ~ 2026.11.22"}])
    assert (d["period_start"], d["period_end"]) == ("2026-09-14", "2026-11-22")


def test_invalid_and_missing():
    assert extract_dates([{"text": "효력일 2026-13-40"}])["effective_date"] is None
    assert extract_dates([{"text": "날짜 언급 없음"}])["effective_date"] is None


def test_effect_status():
    assert effect_status({"effective_date": "2026-10-05"}, "2026-10-01") == "not_yet_effective"
    assert effect_status({"effective_date": "2026-10-05"}, "2026-10-05") == "effective"
    assert effect_status({"period_start": "2026-09-01"}, "2026-10-01") == "effective"
    assert effect_status({}, "2026-10-01") == "unknown"
    assert effect_status({"effective_date": "2026-10-05"}, None) == "unknown"


def test_baseline_docs_carry_text_dates():
    co = next(d for d in list_baseline_docs("P-COM-101:BL-2")["documents"] if d["doc_id"].startswith("CO-A"))
    assert co["text_dates"]["effective_date"] == "2026-10-05"


def test_open_document_fills_effective_date():
    doc = {"document_id": "CO", "items": [{"item_id": "i1", "heading": "제3조", "text": "효력 발생일은 2026-10-05이다."}]}
    assert open_document(doc)["effective_date"] == "2026-10-05"
