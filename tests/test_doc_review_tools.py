"""문서 검토 규칙 도구 (#90)."""
import pytest

from app.tools.doc_review import compare_values, extract_values, item_code, item_map, open_document, version_diff
from eval.doc_review.rule_baseline import run


def _doc(no, items, doc="D-1", status="READY"):
    return {"document_id": doc, "version_no": no, "extraction_status": status,
            "items": [{"item_id": f"B1-{i:05d}", "code": c, "heading": f"[문서] > {t}", "text": x, "page": 1} for i, (c, t, x) in enumerate(items, 1)]}


def test_item_code_prefers_heading_and_skips_document_number():
    assert item_code("[계약서] > 제3조 계약 기간 (C-03)") == "제3조"
    assert item_code("[명세] > A-F12 관리자 엑셀 다운로드") == "A-F12"
    assert item_code("[명세 CTR-A-001-v1.0] > 머리말", "문서번호 CTR-A-001-v1.0") is None
    assert item_code("[명세] > 기능", "[TB-F10][IN] 일정 저장") == "TB-F10"


def test_item_map_and_open_document_hide_text():
    items = item_map([{"section_id": "B1-00001", "heading": "[계약서] > 제4조 계약 대금", "text": "금 60,000,000원", "page": 2}])
    assert items[0]["code"] == "제4조"
    opened = open_document({"document_id": "CTR", "version_no": 1, "items": items})
    assert opened["items"][0] == {"item_id": "B1-00001", "code": "제4조", "title": "제4조 계약 대금", "page": 2}
    assert opened["extraction_status"] == "READY"


def test_extract_and_compare_values():
    values = {v["kind"]: v["value"] for v in extract_values("2026.11.23까지 12주, 금 60,000,000원(10%), 쿠폰 1개, 120만원")}
    assert values["date"] == "2026-11-23" and values["duration"] == "12주" and values["percent"] == 10.0 and values["count"] == "1개"
    assert 60_000_000 in [v["value"] for v in extract_values("금 60,000,000원, 120만원")] and 1_200_000 in [v["value"] for v in extract_values("120만원")]
    changes = compare_values("금 60,000,000원, 2026-11-23 종료", "금 66,000,000원, 2026-11-23 종료")
    assert changes == [{"kind": "amount", "before": "60,000,000원", "after": "66,000,000원", "context": changes[0]["context"]}]


def test_version_diff_statuses():
    v1 = _doc(1, [("A-1", "로그인", "이메일 로그인. 포함 (IN)"), ("A-2", "쿠폰", "쿠폰 1개 적용"), ("A-3", "결제", "결제 오류 안내"), (None, "주석", "본 문서는 합성 데이터입니다")])
    v2 = _doc(2, [("A-1", "로그인", "이메일 로그인. 포함 (IN)"), ("A-2", "쿠폰", "쿠폰 2개 적용"), ("A-4", "CSV", "주문 CSV 내보내기"), (None, "주석", "본 문서는  합성 데이터입니다.")])
    by = {i["key"]: i["summary"]["change"] for i in version_diff([v2, v1])["items"]}  # 순서와 무관하게 번호로 정렬
    assert by["A-1"] == "same" and by["A-2"] == "modified" and by["A-3"] == "removed" and by["A-4"] == "added"
    assert by["B1-00004"] == "minor"


def test_version_diff_reverted_scope_word_and_unchecked():
    v1 = _doc(1, [("C-3", "기간", "2026-11-23까지"), ("F-1", "범위", "배송 링크 제외 (OUT)")])
    v2 = _doc(2, [("C-3", "기간", "2026-12-31까지"), ("F-1", "범위", "배송 링크 포함 (IN)")])
    v3 = _doc(3, [("C-3", "기간", "2026-11-23까지"), ("F-1", "범위", "배송 링크 포함 (IN)")])
    items = {i["key"]: i for i in version_diff([v1, v2, v3])["items"]}
    assert items["C-3"]["summary"]["change"] == "reverted" and items["C-3"]["summary"]["value_change"]
    assert items["F-1"]["summary"]["change"] == "modified"
    assert set(items["F-1"]["history"][0]["scope_words_changed"]) >= {"IN", "OUT"}
    failed = version_diff([v1, _doc(2, [], status="FAILED")])
    assert failed["unchecked"] == [{"version_no": 2, "reason": "추출 실패 또는 항목 없음"}] and failed["items"] == []


def test_version_diff_limits():
    with pytest.raises(ValueError):
        version_diff([_doc(1, [])])
    with pytest.raises(ValueError):
        version_diff([_doc(n, [("A", "a", "x")]) for n in range(1, 7)])
    with pytest.raises(ValueError):
        version_diff([_doc(1, [("A", "a", "x")]), _doc(2, [("A", "a", "x")], doc="D-2")])


def test_rule_baseline_solves_version_cases():
    s = run()["score"]
    assert s["recall_required"] == 1.0 and s["forbidden_hits"] == 0 and s["extra_for_review"] == 0


def test_version_diff_handles_renumbering_and_code_reuse():
    v1 = _doc(1, [("제2조", "범위", "## 제2조 과업 범위\n발주자 요청 기능을 구현한다"), ("제3조", "기간", "## 제3조 계약 기간\n2026-09-01부터 2026-11-23까지"),
                  ("제4조", "대금", "## 제4조 계약 대금\n금 60,000,000원")])
    v2 = _doc(2, [("제2조", "범위", "## 제2조 과업 범위\n발주자 요청 기능을 구현한다"), ("제3조", "장소", "## 제3조 수행 장소\n원격 근무를 원칙으로 한다"),
                  ("제4조", "기간", "## 제4조 계약 기간\n2026-09-01부터 2026-11-23까지"), ("제5조", "대금", "## 제5조 계약 대금\n금 60,000,000원")])
    items = version_diff([v1, v2])["items"]
    renumbered = [i for i in items if i["summary"]["change"] == "renumbered"]
    assert sorted(i["codes"][0] for i in renumbered) == ["제3조", "제4조"]  # 기존 제3·4조는 번호만 밀림
    assert all(i["summary"]["code_changed"] for i in renumbered)
    assert [i["code"] for i in items if i["summary"]["change"] == "added"] == ["제3조"]  # 새 제3조(수행 장소)만 추가

    w1 = _doc(1, [("A-F09", "결제 오류", "| A-F09 | 포함 (IN) | 결제 오류를 안내한다. 검수기준: 오류 문구 노출 |")])
    w2 = _doc(2, [("A-F09", "위시리스트", "| A-F09 | 포함 (IN) | 관심 상품을 저장한다. 검수기준: 100개 저장 |")])
    reused = version_diff([w1, w2])["items"]
    assert sorted(i["summary"]["change"] for i in reused) == ["added", "removed"]
    assert all(i["summary"].get("code_reused") for i in reused)
