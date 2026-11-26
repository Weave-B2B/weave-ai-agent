"""새 파서 청킹(b1_v3): 표 항목 식별자 분리·문서 식별자·빈 경로·당사자 추출."""
import pytest

from app.indexing import b1_units, pdf_r2  # noqa: E402

FSP = [
    "## 모노마켓 커머스 리뉴얼 기능명세서\n\n기능명세서 | 문서번호 FSP-A-001-v1.0\n\n"
    "| 프로젝트 | 모노마켓 | 문서 유형 | 기능명세서 |\n|---|---|---|---|\n| 문서번호 | FSP-A-001-v1.0 | 버전 | 1.0 |\n\n"
    "## 01. 기능 요구사항 적용 규칙\n\nIN은 최초 계약 포함, OUT은 최초 계약 제외다.\n\n"
    "| 섹션 / 기능 ID | 범위 | 기능 상세 및 검수기준 |\n|---|---|---|\n"
    "| F-01 | 포함 | [A-F01][IN] 회원 이메일 로그인: 이메일 로그인. 검수기준: 정상 계 |\n"
    "| A-F01 | (IN) | 정 로그인. |\n"
    "| F-02 | 포함 | [A-F02][IN] 장바구니: 상품 추가와 삭제. 검수기준: 합계 갱신. |\n"
    "| F-03 | 제외 | [A-F03][OUT] 쿠폰 중복 적용: 쿠폰 2개 동시 적용. |\n",
]


def test_row_key():
    assert b1_units.row_key("| F-01 | 포함 | [A-F01][IN] 회원 이메일 로그인: 설명 |") == "A-F01 회원 이메일 로그인"
    assert b1_units.row_key("| A-F01 | (IN) | 정 로그인. |") is None


def test_feature_table_rows_become_one_chunk_each():
    _, chunks = pdf_r2.build_chunks("FSP", FSP)
    by_key = {c["heading"].rsplit(" > ", 1)[-1].rstrip("]"): c for c in chunks}
    assert {"A-F01 회원 이메일 로그인", "A-F02 장바구니", "A-F03 쿠폰 중복 적용"} <= by_key.keys()
    login = by_key["A-F01 회원 이메일 로그인"]["text"]
    assert "정 로그인." in login and "장바구니" not in login          # 나뉜 행은 같은 청크, 다른 기능은 다른 청크
    assert "섹션 / 기능 ID" not in login                               # 머리글은 반복하지 않는다
    assert " > 01. 기능 요구사항 적용 규칙 > A-F01" in by_key["A-F01 회원 이메일 로그인"]["heading"]


def test_identity_excludes_meta_table_and_cover_has_no_empty_path():
    _, chunks = pdf_r2.build_chunks("FSP", FSP)
    assert all("|---" not in c["heading"] and "| 프로젝트" not in c["heading"] for c in chunks)
    assert not any(c["heading"].endswith(" > ]") for c in chunks)


def test_table_without_row_keys_keeps_previous_behavior():
    b = dict(kind="table", rows=[(0, 5, "| a | b |"), (6, 11, "|---|---|"), (12, 17, "| 1 | 2 |")])
    assert b1_units.table_records(b) is None


def test_padded_number_line_is_section_but_plain_list_is_not():
    pages = ["## 변경합의서\n\n01. 변경 대상\n\nCO-01\n\n리뷰 기능을 승인한다.\n\n02. 합의 대금·일정\n\nCO-02\n\n대금은 480만원이다.\n\n"
             "## 04. 검수\n\n1. 별점 등록\n2. 사진 3장 첨부\n"]
    _, chunks = pdf_r2.build_chunks("CO", pages)
    paths = [c["heading"] for c in chunks]
    assert any(p.endswith("> 01. 변경 대상]") for p in paths)
    assert any(p.endswith("> 02. 합의 대금·일정]") for p in paths)
    assert not any("> 1. 별점 등록" in p for p in paths)          # 일반 목록은 절이 아니다


def test_parties_from_sentence_style_cover_line():
    from app.indexing.path_extractor import extract_parties
    head = ("발주자: 주식회사 모노마켓. 수행사: 주식회사 플로우웍스. 사업명: 커머스 리뉴얼.\n"
            "발주자 추가 요청은 원문과 접수일을 기록한다. 수행사는 범위·공수·금액을 검토한다.\n"
            "## 06. 발주사 제공 사항\n")
    assert [(p["role"], p["name"]) for p in extract_parties(head)] == [("발주자", "모노마켓"), ("수행사", "플로우웍스")]


def test_parties_ignore_prose_and_read_signature_table():
    from app.indexing.path_extractor import extract_parties
    head = ("발주사 권한자 김서진, 수행사 권한자 이도윤이 2026-11-03 서명한 것으로 설정한다.\n"
            "| 발주사 | 김서진 | 2026-11-03 | 서명 완료 |\n|---|---|---|---|\n| 수행사 | 이도윤 | 2026-11-03 | 서명 완료 |\n")
    assert [(p["role"], p["name"]) for p in extract_parties(head)] == [("발주사", "김서진"), ("수행사", "이도윤")]


def test_unnumbered_headings_and_id_column_tables():
    pages = ["## 커머스 기능명세서\n\n문서번호 FSP-X\n\n## Ⅰ. 적용 규칙\n\nIN은 포함이다.\n\n"
             "| 번호 | 기능 ID | 기능명 | 내용 |\n|---|---|---|---|\n"
             "| 1.1 | A-F01 | 회원 로그인 | 이메일 로그인. |\n| 1.2 | A- F02 | 장바구니 | 추가와 삭제. |\n\n"
             "## 계약 당사자\n\n발주자는 모노마켓이다.\n"]
    _, chunks = pdf_r2.build_chunks("F", pages)
    heads = [c["heading"] for c in chunks]
    assert heads[0] == "[커머스 기능명세서 / 문서번호 FSP-X]"          # 첫 절 제목이 식별자에 섞이지 않는다
    assert any(h.endswith("> Ⅰ. 적용 규칙 > A-F01 회원 로그인]") for h in heads)
    assert any(h.endswith("> A-F02 장바구니]") for h in heads)           # 'A- F02' 공백 정리
    assert any(h.endswith("> 계약 당사자]") for h in heads)              # 번호 없는 제목도 절


def test_wrapped_table_row_is_merged_into_one_logical_row():
    page = ("| ID | 범위 | 내용 |\n|---|---|---|\n"
            "| F-02 | 포함 | [A-F02][IN] 상품 목록·상세: 상품 목록에 |\n| A-F02 | (IN) | 서 상세 진입 가능. |\n")
    merged = pdf_r2.merge_wrapped_rows(page)
    assert "| F-02 A-F02 | 포함 (IN) | [A-F02][IN] 상품 목록·상세: 상품 목록에 서 상세 진입 가능. |" in merged
    _, chunks = pdf_r2.build_chunks("F", ["## 명세\n\n## 01. 기능\n\n" + page])
    row = next(c for c in chunks if c["heading"].endswith("A-F02 상품 목록·상세]"))
    assert "목록에 서 상세" in row["text"] and row["line_start"] > 2       # 머리글 줄이 아니라 항목 행부터
