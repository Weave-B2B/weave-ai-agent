"""배포용 WBS·인력 투입 계획 엑셀(data/templates)과 wbs-v1 해석 규칙.

모노마켓 예시는 일정 골든 벡터 monomarket_wbs_coupon_change와 같은 요청이 되어야 한다. 그 사례의 계산 결과는
Backend ScheduleV1GoldenTest가 검증하므로, 여기서는 엑셀 → 요청 변환과 예시 결과 시트의 일치를 확인한다.
양식을 바꾸면 scripts/build_impact_workbooks.py로 다시 만든다.
"""
import importlib.util
import io
import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.agents.agent3.contracts import ProvisionalLine, ScheduleHint
from app.tools import schedule_plan, wbs_workbook
from app.tools.errors import ToolInputError
from app.tools.impact_workbook import _sheets, parse_workbook

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "data" / "templates"
GOLDEN = {c["name"]: c for c in json.loads((ROOT / "tests/fixtures/schedule_v1_golden.json").read_text(encoding="utf-8"))["cases"]}
CASE = GOLDEN["monomarket_wbs_coupon_change"]


def _builder():
    spec = importlib.util.spec_from_file_location("build_impact_workbooks", ROOT / "scripts/build_impact_workbooks.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError:  # openpyxl이 없는 환경
        pytest.skip("openpyxl 없음")
    return module


def example_bytes():
    return (TEMPLATES / "impact-workbook-example.xlsx").read_bytes()


def change_lines():
    """예시 결과 시트의 CR-A-009 작업 행. 모델이 엑셀 이름 그대로 제안했다고 가정한다."""
    lines = [("중복 적용 정책(조합·순서·상한) 정의", "PM", 0.5, ScheduleHint(assignee="김민서")),
             ("쿠폰 2장 선택·할인 내역 표시", "FE", 1.0,
              ScheduleHint(assignee="이준호", after=["결제·쿠폰·관리자 화면"], before=["통합 테스트 (M4)"])),
             ("쿠폰 조합 검증·할인 계산·결제 금액 검증", "BE", 2.5,
              ScheduleHint(assignee="최지훈", after=["장바구니·결제·쿠폰 API"], before=["통합 테스트 (M4)"])),
             ("쿠폰 조합·결제 금액 회귀 테스트", "QA", 1.5, ScheduleHint(extends_task="통합 테스트 (M4)"))]
    return [(f"E1L{i}", ProvisionalLine(task=t, item_ids=["I1"], role=r, md=md, assumption="", schedule_hint=h))
            for i, (t, r, md, h) in enumerate(lines, 1)]


def normalized(value):
    return json.loads(json.dumps(value), parse_float=schedule_plan._num, parse_int=schedule_plan._num)


def test_monomarket_example_becomes_the_golden_request():
    data = parse_workbook(example_bytes())
    assert data["format"] == "wbs-v1" and data["missing"] == []
    request, problems = schedule_plan.build_request(data, change_lines(), "8", "44")
    assert problems == []
    assert normalized(request) == normalized(CASE["request"])


def test_progress_estimates_and_done_tasks():
    data = parse_workbook(example_bytes())
    est = {e["task"]: e for e in data["estimates"]}
    # 계획 9/14~10/8 근무일 16일(추석·개천절 대체공휴일 제외) × 8h = 128h, 진척률 90% → 12.8h → 13h
    assert est["로그인·상품 목록/상세 API"] == {"task": "로그인·상품 목록/상세 API", "basis": "progress", "progress": 0.9,
                                           "planned_hours": 128.0, "remaining_hours": 13.0}
    assert est["결함 수정 (BE)"]["basis"] == "direct" and est["결함 수정 (BE)"]["remaining_hours"] == 24
    assert {e["task"] for e in data["estimates"] if e["basis"] == "done"} == {"착수·요구사항 확정 (M1)", "화면 설계", "기능 설계 승인 (M2)"}
    names = {t["name"] for t in data["tasks"]}
    assert "화면 설계" not in names
    assert all(p in names for t in data["tasks"] for p in t["predecessors"])  # 끝난 선행 작업은 빠진다
    assert any("진척률" in a for a in data["assumptions"])


def test_monthly_rates_become_person_exceptions():
    data = parse_workbook(example_bytes())
    base, derived = wbs_workbook.expand_staffing(data)
    assert base["정수빈"] == 8 and base["박서연"] == 0  # 마지막 달(11월) 투입률이 기본값
    qa = {e["date"]: e["hours"] for e in derived if e["person"] == "정수빈"}
    assert qa["2026-10-12"] == 4 and "2026-11-02" not in qa  # 10월 0.5, 11월 1.0
    assert "2026-10-09" not in qa  # 한글날은 전체 휴무(0h) 그대로
    ux = {e["date"]: e["hours"] for e in derived if e["person"] == "박서연"}
    assert ux["2026-10-12"] == 4 and "2026-10-13" not in ux  # 개인 휴가는 시트 값 그대로 쓴다


def test_example_result_sheet_matches_golden_result():
    rows = {cells["A"][0]: cells.get("B", ("", False))[0] for _, cells in _sheets(example_bytes())["예시 결과"] if "A" in cells}
    want = CASE["expected"]
    assert rows["예상 완료일"] == want["estimated_end_date"]
    assert rows["변경 작업 시작일"] == want["change_start_date"]
    assert rows["현재 계획 완료일"] == want["planned_end"] and rows["기존 작업만의 완료일"] == want["baseline"]["end"]
    assert rows["일정 영향 (계획 대비)"] == f"{want['impact_business_days']}영업일"
    assert rows["일정 영향 (기존 작업만 대비)"] == f"{want['impact_vs_baseline_business_days']}영업일"
    assert rows["추가 공수"] == f"5.5 MD ({want['change_hours']}h)"


def test_builder_constants_match_golden_case():
    b = _builder()
    assert b.EXPECTED["changed_end"] == CASE["expected"]["estimated_end_date"]
    assert b.EXPECTED["impact"] == CASE["expected"]["impact_business_days"]


def test_empty_template_is_recognized_and_asks_for_required_values():
    with pytest.raises(ToolInputError, match="진행률 기준일을 입력해주세요"):
        parse_workbook((TEMPLATES / "impact-workbook-template.xlsx").read_bytes())


def test_new_workbook_labels_and_guidance_are_present():
    sheets = _sheets(example_bytes())
    cell = lambda sheet, row, col: next(c for n, c in sheets[sheet] if n == row)[col][0]
    assert cell("WBS", 8, "C") == "직군"
    assert cell("WBS", 8, "H") == "진행률"
    assert cell("WBS", 8, "F") == "실제 시작일"
    assert cell("WBS", 8, "G") == "실제 종료일"
    assert cell("WBS", 4, "D") == "아래 진행률을 업데이트한 날짜를 꼭 입력해주세요. 남은 진행률을 이 날짜 기준으로 계산합니다."
    assert cell("WBS", 2, "A") == "WBS는 프로젝트의 작업, 담당자, 일정과 진행 상황을 정리한 표입니다."
    assert "매주 확인" in cell("WBS", 3, "A")
    assert cell("인력 투입 계획", 2, "A") == "칸의 숫자는 투입률입니다. 1.0은 한 달 내내 전담, 0.5는 절반(하루 4시간 정도)입니다."
    assert cell("인력 투입 계획", 3, "A") == "1 MM(인월)은 한 사람이 한 달 일한 양으로, 보통 근무일 20~22일 정도입니다."
    assert cell("인력 투입 계획", 6, "G") == "총투입량(MM)"


def test_example_names_and_mm_totals_are_consistent():
    data = parse_workbook(example_bytes())
    assert [p["name"] for p in data["people"]] == ["김민서", "박서연", "이준호", "최지훈", "정수빈"]
    assert {t["owner"] for t in data["tasks"]} <= {p["name"] for p in data["people"]}
    staff = dict(_sheets(example_bytes())["인력 투입 계획"])
    assert staff[12]["A"][0] == "월별 투입규모 합계"
    assert [float(staff[r]["G"][0]) for r in range(7, 12)] == [1.5, 1.5, 3, 3, 1.5]
    assert [float(staff[12][c][0]) for c in "CDEG"] == [3.5, 3.5, 3.5, 10.5]
    assert all(staff[12][c][1] for c in "CDEG")


def test_display_totals_are_not_read_as_people_or_calculation_inputs():
    payload = _edit("인력 투입 계획", 12, "C", "999")
    assert parse_workbook(payload) == parse_workbook(example_bytes())


@pytest.mark.parametrize("example", [False, True])
def test_rate_card_contains_registration_fields_and_preserves_blank_prices(example):
    name = "impact-workbook-example.xlsx" if example else "impact-workbook-template.xlsx"
    rows = dict(_sheets((TEMPLATES / name).read_bytes())["단가표"])
    assert rows[4]["A"][0] == "적용 시작일"
    assert rows[6]["B"][0] == "KRW"
    assert float(rows[5]["B"][0]) == 8 and rows[5]["B"][1]  # 인력 시트와 연동
    rates = json.loads((ROOT / "data/kb/rate_cards.json").read_text(encoding="utf-8"))["rate_cards"]["RC-SYNTH-2026H2-v2"]["rates"]
    assert [rows[r]["A"][0] for r in range(9, 14)] == list(rates)
    for row, code in zip(range(9, 14), rates):
        if example:
            assert float(rows[row]["C"][0]) == rates[code]["cost"]
            assert float(rows[row]["D"][0]) == rates[code]["price"]
        else:
            assert rows[row].get("C", ("", False))[0] == ""
            assert rows[row].get("D", ("", False))[0] == ""
    assert "자동 등록되지 않습니다" in rows[15]["A"][0]
    assert "합성" in rows[16]["A"][0] if example else rows[4].get("B", ("", False))[0] == ""


def test_workbook_rates_do_not_change_official_schedule_or_price_inputs():
    expected = parse_workbook(example_bytes())
    assert parse_workbook(_edit("단가표", 12, "D", "1")) == expected
    assert parse_workbook(_edit("단가표", 12, "C", "99999999")) == expected


def test_display_role_matches_the_staffing_plan_and_does_not_override_it():
    sheets = _sheets(example_bytes())
    expected = wbs_workbook.parse(sheets)
    roles = {p["name"]: p["role"] for p in expected["people"]}
    for row, cells in sheets["WBS"]:
        if 9 <= row <= 24:
            assert cells["C"][0] == roles[cells["B"][0]]
            assert cells["C"][1]  # 담당자를 참조하는 보기용 수식
            cells["C"] = ("보기용 다른 직군", True)
    assert wbs_workbook.parse(sheets) == expected


def test_wbs_without_the_optional_display_role_column_is_still_supported():
    sheets = _sheets(example_bytes())
    expected = wbs_workbook.parse(sheets)
    sheets["WBS"] = [
        (row, {wbs_workbook._column(i): cells.get(wbs_workbook._column(i if i < 2 else i + 1), ("", False))
               for i in range(10)}) if row >= 8 else (row, cells)
        for row, cells in sheets["WBS"]
    ]
    assert wbs_workbook.parse(sheets) == expected


def test_customer_lines_say_remaining_hours_are_estimates():
    plan = {"estimated_end_date": "2026-12-02", "baseline": {"end": "2026-11-24"}, "planned_end": "2026-11-24",
            "impact_business_days": 6, "impact_vs_baseline_business_days": 6}
    data = parse_workbook(example_bytes())
    text = "\n".join(schedule_plan.customer_lines({"plan": plan, "assumptions": data["assumptions"]}))
    assert "WBS의 진척률을 기준으로 추정" in text and "2026-11-24 대비 6영업일" in text


# ---- 잘못된 입력은 보정하지 않고 거절 ----

def _edit(sheet, row, col, value, source=None):
    """기존 입력 계약의 열 이름으로 한 칸을 수정한다. 보기용 C열은 건너뛴다."""
    from xml.etree import ElementTree as ET
    payload = example_bytes() if source is None else source
    if sheet == "WBS" and row >= 8:
        head = next(cells for n, cells in _sheets(payload)["WBS"] if n == 8)
        if head.get("C", ("", False))[0] == "직군" and col in "CDEFGHIJ":
            col = chr(ord(col) + 1)
    src, out = ZipFile(io.BytesIO(payload)), io.BytesIO()
    sheet_number = list(_sheets(payload)).index(sheet) + 1
    target = f"xl/worksheets/sheet{sheet_number}.xml"
    ref = f"{col}{row}"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    tag = lambda name: f"{{{ns}}}{name}"
    with ZipFile(out, "w") as z:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == target:
                root = ET.fromstring(body)
                rows = root.find(tag("sheetData"))
                current = rows.find(f"{tag('row')}[@r='{row}']")
                if current is None:
                    current = ET.SubElement(rows, tag("row"), {"r": str(row)})
                old = current.find(f"{tag('c')}[@r='{ref}']")
                if old is not None:
                    current.remove(old)
                cell = ET.SubElement(current, tag("c"), {"r": ref, "t": "inlineStr"})
                ET.SubElement(ET.SubElement(cell, tag("is")), tag("t")).text = str(value)
                body = ET.tostring(root, encoding="utf-8")
            z.writestr(info, body)
    return out.getvalue()


@pytest.mark.parametrize("base, start, end, progress", [
    ("진척률 기준일", "실적 시작일", "실적 종료일", "진척률"),
    ("진행률 기준일", "실제 시작일", "실제 종료일", "진행률"),
    ("진척률 기준일", "실제 시작일", "실적 종료일", "진행률"),
])
def test_previous_and_readable_labels_have_the_same_schedule_input(base, start, end, progress):
    payload = example_bytes()
    for row, col, value in [(4, "A", base), (8, "E", start), (8, "F", end), (8, "G", progress)]:
        payload = _edit("WBS", row, col, value, payload)
    assert parse_workbook(payload) == parse_workbook(example_bytes())


@pytest.mark.parametrize("sheet, row, col, value, message", [
    ("WBS", 12, "H", "확인 필요", "선행 작업을 확인"),          # 표에 없는 선행 작업
    ("WBS", 12, "B", "DBA 담당자", "인력 투입 계획에 없는 담당자"),
    ("WBS", 9, "G", "50%", "진행률은 100%"),                  # 실제 종료일이 있는데 50%
    ("WBS", 12, "G", "150", "허용 범위"),
    ("WBS", 12, "D", "", "계획 종료일"),                       # 추정에 필요한 계획 기간 없음
    ("WBS", 9, "A", "로그인·상품 화면", "중복"),
    ("인력 투입 계획", 6, "D", "2026-12", "순서대로"),           # 월이 빠지거나 뒤섞임
    ("휴무", 5, "A", "없는 사람", "인력 투입 계획에 없는 담당자"),
])
def test_bad_inputs_fail_closed(sheet, row, col, value, message):
    with pytest.raises(ToolInputError, match=message):
        parse_workbook(_edit(sheet, row, col, value))


def test_percent_entry_forms_are_the_same():
    assert wbs_workbook._ratio("0.7", "x") == wbs_workbook._ratio("70%", "x") == 0.7
    assert wbs_workbook._ratio("1", "x") == 1.0 and wbs_workbook._ratio("", "x") == 0.0


@pytest.mark.parametrize("value", ["1.1", "1.2", "110%", "120%", "70", "-0.1", "NaN", "Infinity"])
def test_out_of_range_ratios_are_rejected_without_rescaling(value):
    with pytest.raises(ToolInputError):
        parse_workbook(_edit("WBS", 12, "G", value))
    with pytest.raises(ToolInputError):
        parse_workbook(_edit("인력 투입 계획", 9, "D", value))


@pytest.mark.parametrize("progress, remaining", [("95%", 1), ("97.5%", 0.5), ("90%", 2), ("94.99%", 1.5)])
def test_remaining_hours_round_only_after_exact_decimal_arithmetic(progress, remaining):
    data = parse_workbook(_edit("WBS", 24, "G", progress))
    result = next(e for e in data["estimates"] if e["task"] == "검수·최종 인수 (M5)")
    assert result["planned_hours"] == 20
    assert result["remaining_hours"] == remaining
    # 구조화 출력은 Decimal 객체 없이 기존 JSON 계약을 유지한다.
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize("changes, message", [
    ([(12, "F", "2026-11-24"), (12, "G", "100%")], "기준일 이후"),
    ([(12, "E", "2026-10-13")], "기준일 이후"),
    ([(12, "E", "날짜 아님")], "날짜 형식"),
    ([(12, "F", "2026-09-10"), (12, "G", "100%")], "실제 시작일보다"),
    ([(12, "C", "2026-10-09"), (12, "D", "2026-10-08"), (12, "I", "13")], "계획 기간"),
])
def test_dates_are_checked_against_the_snapshot_even_for_direct_or_completed_tasks(changes, message):
    payload = example_bytes()
    for row, col, value in changes:
        payload = _edit("WBS", row, col, value, payload)
    with pytest.raises(ToolInputError, match=message):
        parse_workbook(payload)


def test_overlapping_tasks_require_direct_remaining_hours_instead_of_double_counting_capacity():
    payload = _edit("WBS", 13, "C", "2026-10-08")
    with pytest.raises(ToolInputError, match="겹칩니다.*남은 시간을 직접"):
        parse_workbook(payload)
    # 같은 기간의 두 작업 모두 직접 공수를 입력하면 명시한 값을 그대로 사용한다.
    payload = _edit("WBS", 12, "I", "13", payload)
    payload = _edit("WBS", 13, "I", "40", payload)
    data = parse_workbook(payload)
    selected = [e for e in data["estimates"] if e["task"] in ("로그인·상품 목록/상세 API", "검색·필터 API")]
    assert [e["remaining_hours"] for e in selected] == [13, 40]
    assert all(e["basis"] == "direct" for e in selected)


def test_zero_direct_remaining_hours_is_not_replaced_by_progress_estimate():
    data = parse_workbook(_edit("WBS", 12, "I", "0"))
    result = next(e for e in data["estimates"] if e["task"] == "로그인·상품 목록/상세 API")
    assert result["basis"] == "direct" and result["remaining_hours"] == 0


def test_mismatched_workbook_md_hours_blocks_schedule_without_calling_backend(monkeypatch):
    from types import SimpleNamespace
    from app.tools import backend_calc

    data = parse_workbook(_edit("인력 투입 계획", 4, "B", "4"))
    request, problems = schedule_plan.build_request(data, change_lines(), "8", "44")
    assert request is None and "백엔드 공식 기준(8h)" in problems[0]
    monkeypatch.setattr(backend_calc, "calculation_client", lambda _: pytest.fail("단위 불일치에서 계산기를 호출함"))
    out = SimpleNamespace(impact_workbook={"data": data},
                          pricing=SimpleNamespace(hours_per_md=8, total_hours=44),
                          estimates=[SimpleNamespace(lines=[line for _, line in change_lines()])])
    detail = schedule_plan.calculate_plan(out, "rate")
    assert detail["plan"] is None and "1 MD 기준 시간" in detail["unassigned"][0]


def test_existing_delay_fixture_keeps_the_original_work_and_separates_change_impact():
    data = parse_workbook(_edit("WBS", 24, "I", "28"))
    request, problems = schedule_plan.build_request(data, change_lines(), "8", "44")
    delayed = GOLDEN["monomarket_wbs_coupon_existing_delay"]
    assert problems == [] and normalized(request) == normalized(delayed["request"])
    result = delayed["expected"]
    assert result["baseline"]["end"] == "2026-11-26"
    assert result["estimated_end_date"] == "2026-12-04"
    assert result["impact_business_days"] == 8
    assert result["impact_vs_baseline_business_days"] == 6


def test_month_header_accepts_excel_dates():
    assert wbs_workbook._month("2026-09", "x") == (2026, 9)
    assert wbs_workbook._month("2026년 9월", "x") == (2026, 9)
    serial = str((date(2026, 9, 1) - date(1899, 12, 30)).days)  # Excel이 2026-09를 날짜로 바꾼 경우
    assert wbs_workbook._month(serial, "x") == (2026, 9)


def test_empty_inline_string_cell_is_read_as_blank():
    """Excel 외 도구가 빈 문자열 칸을 <is> 없이 저장해도 파일 전체를 거절하지 않는다."""
    src, out = ZipFile(io.BytesIO(example_bytes())), io.BytesIO()
    with ZipFile(out, "w") as z:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = body.replace(b"</row>", b'<c r="Z1" t="inlineStr"/></row>', 1)
            z.writestr(info, body)
    assert parse_workbook(out.getvalue())["format"] == "wbs-v1"
