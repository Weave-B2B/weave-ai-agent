"""일정·공수 엑셀(영향 산정 자료)의 WBS·인력 투입 계획 빈 양식과 모노마켓 작성 예시를 만든다.

    pip install openpyxl   # 이 스크립트에서만 쓴다. 서버·테스트는 필요 없음
    python3 scripts/build_impact_workbooks.py

결과: data/templates/impact-workbook-template.xlsx(빈 양식), impact-workbook-example.xlsx(모노마켓 예시).
칸 위치·제목 문구는 app/tools/wbs_workbook.parse가 읽는 형식과 맞춰야 한다. 바꾸면 파서와
tests/test_impact_workbook_template.py를 함께 확인한다. 예시 결과는 일정 골든 벡터의
monomarket_wbs_coupon_change 사례(Backend 계산기 실행값)와 같아야 한다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

OUT = Path(__file__).resolve().parents[1] / "data" / "templates"
TASK_ROWS, PEOPLE_ROWS, HOLIDAY_ROWS = 80, 30, 60
TEMPLATE_MONTHS = ["", "", "", "", "", ""]

INPUT = PatternFill("solid", fgColor="FFF4C2")
WEEKLY = PatternFill("solid", fgColor="DBEAFE")
HEAD = PatternFill("solid", fgColor="DCE6F2")
THIN = Side(style="thin", color="B7B7B7")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
TITLE, BOLD, NOTE = Font(bold=True, size=14), Font(bold=True), Font(color="595959", size=10)
DATE_FMT, PCT_FMT = "yyyy-mm-dd", "0%"
D = date

# 데이터셋 모노마켓(P-COM-101): 계약 2026-09-01~11-23, 변경합의서 CO-A-001로 오픈일 11/24.
# 기능 ID는 SOW-A-001·FSP-A-001의 포함 기능. 진행률·투입률·담당자 이름은 검증용 가정이다.
EXAMPLE = {
    "base": D(2026, 10, 12), "planned_end": D(2026, 11, 24), "weekdays": "월,화,수,목,금", "hpm": 8,
    "months": ["2026-09", "2026-10", "2026-11"],
    "people": [("김민서", "PM", [0.5, 0.5, 0.5]), ("박서연", "UX", [1.0, 0.5, 0.0]),
               ("이준호", "FE", [1.0, 1.0, 1.0]), ("최지훈", "BE", [1.0, 1.0, 1.0]),
               ("정수빈", "QA", [0.0, 0.5, 1.0])],
    # 작업명, 담당자, 계획 시작, 계획 종료, 실제 시작, 실제 종료, 진행률, 선행, 남은 시간(직접), 비고
    "tasks": [
        ("착수·요구사항 확정 (M1)", "김민서", D(2026, 9, 1), D(2026, 9, 15), D(2026, 9, 1), D(2026, 9, 15), 1.0, "", None, ""),
        ("화면 설계", "박서연", D(2026, 9, 2), D(2026, 9, 30), D(2026, 9, 2), D(2026, 9, 30), 1.0, "", None, ""),
        ("기능 설계 승인 (M2)", "김민서", D(2026, 9, 16), D(2026, 10, 1), D(2026, 9, 16), D(2026, 10, 1), 1.0, "착수·요구사항 확정 (M1)", None, ""),
        ("로그인·상품 목록/상세 API", "최지훈", D(2026, 9, 14), D(2026, 10, 8), D(2026, 9, 14), None, 0.9, "", None, "A-F01, A-F02. 계획보다 늦음"),
        ("검색·필터 API", "최지훈", D(2026, 10, 12), D(2026, 10, 16), None, None, 0.0, "로그인·상품 목록/상세 API", None, "A-F03, A-F10"),
        ("장바구니·결제·쿠폰 API", "최지훈", D(2026, 10, 19), D(2026, 10, 28), None, None, 0.0, "검색·필터 API", None, "A-F04~F06, A-F09"),
        ("관리자 주문 조회 API", "최지훈", D(2026, 10, 29), D(2026, 10, 30), None, None, 0.0, "장바구니·결제·쿠폰 API", None, "A-F07"),
        ("로그인·상품 화면", "이준호", D(2026, 9, 21), D(2026, 10, 14), D(2026, 9, 21), None, 0.7, "화면 설계", None, "A-F01, A-F02, A-F08"),
        ("검색·필터·장바구니 화면", "이준호", D(2026, 10, 15), D(2026, 10, 23), None, None, 0.0, "로그인·상품 화면", None, "A-F03, A-F04, A-F08"),
        ("결제·쿠폰·관리자 화면", "이준호", D(2026, 10, 26), D(2026, 10, 30), None, None, 0.0, "검색·필터·장바구니 화면", None, "A-F05~F07, A-F09"),
        ("배송사 조회 링크", "이준호", D(2026, 11, 2), D(2026, 11, 3), None, None, 0.0, "결제·쿠폰·관리자 화면", None, "변경합의서 CO-A-001"),
        ("테스트 케이스 작성", "정수빈", D(2026, 10, 19), D(2026, 10, 30), None, None, 0.0, "기능 설계 승인 (M2)", None, ""),
        ("통합 테스트 (M4)", "정수빈", D(2026, 11, 4), D(2026, 11, 13), None, None, 0.0,
         "관리자 주문 조회 API; 배송사 조회 링크; 테스트 케이스 작성", None, "A-F10 성능 시험 포함"),
        ("결함 수정 (BE)", "최지훈", D(2026, 11, 4), D(2026, 11, 13), None, None, 0.0, "관리자 주문 조회 API", 24, "남은 시간 직접 입력 예시"),
        ("결함 수정 (FE)", "이준호", D(2026, 11, 4), D(2026, 11, 13), None, None, 0.0, "배송사 조회 링크", 24, "남은 시간 직접 입력 예시"),
        ("검수·최종 인수 (M5)", "김민서", D(2026, 11, 18), D(2026, 11, 24), None, None, 0.0,
         "통합 테스트 (M4); 결함 수정 (BE); 결함 수정 (FE)", None, "오픈일 11/24(CO-A-001)"),
    ],
    "holidays": [("전체", D(2026, 9, 24), 0, "추석 연휴"), ("전체", D(2026, 9, 25), 0, "추석"),
                 ("전체", D(2026, 10, 5), 0, "개천절 대체공휴일"), ("전체", D(2026, 10, 9), 0, "한글날"),
                 ("박서연", D(2026, 10, 13), 0, "개인 휴가 예시")],
}

# Agent 3가 CR-A-009(쿠폰 2장 중복 적용)에 대해 테스트 공수 기준으로 만드는 작업 행과 일정 배치 제안.
CHANGE_LINES = [
    ("중복 적용 정책(조합·순서·상한) 정의", "PM", 0.5, "김민서", [], [], None),
    ("쿠폰 2장 선택·할인 내역 표시", "FE", 1.0, "이준호", ["결제·쿠폰·관리자 화면"], ["통합 테스트 (M4)"], None),
    ("쿠폰 조합 검증·할인 계산·결제 금액 검증", "BE", 2.5, "최지훈", ["장바구니·결제·쿠폰 API"], ["통합 테스트 (M4)"], None),
    ("쿠폰 조합·결제 금액 회귀 테스트", "QA", 1.5, None, [], [], "통합 테스트 (M4)"),
]
EXPECTED = {"baseline_end": "2026-11-24", "changed_end": "2026-12-02", "change_start": "2026-10-12",
            "impact": 6, "impact_vs_baseline": 6, "change_md": "5.5", "change_hours": "44"}


def _head(ws, row, labels, start_col=1):
    for col, label in enumerate(labels, start_col):
        c = ws.cell(row, col, label)
        c.font, c.fill, c.border = BOLD, HEAD, BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")


def _input(ws, row, col, value=None, fmt=None):
    c = ws.cell(row, col, None if value == "" else value)
    c.fill, c.border = INPUT, BOX
    if fmt:
        c.number_format = fmt
    return c


def _notes(ws, start_row, lines, col=1):
    for i, text in enumerate(lines):
        if text:
            ws.cell(start_row + i, col, text).font = NOTE


def _validation(ws, kind, ranges, error, **kw):
    dv = DataValidation(type=kind, allow_blank=True, showErrorMessage=True, errorTitle="입력 확인", error=error, **kw)
    ws.add_data_validation(dv)
    for r in ranges:
        dv.add(r)


def guide_sheet(wb, example):
    ws = wb.active
    ws.title = "작성 안내"
    ws.column_dimensions["A"].width = 120
    ws["A1"] = "일정·공수 자료 작성 안내" + (" (모노마켓 작성 예시)" if example else "")
    ws["A1"].font = TITLE
    lines = [
        "주간보고에 쓰는 WBS와 인력 투입 계획을 그대로 옮겨 적는 양식입니다. Agent 3가 변경 작업을 이 계획에 끼워 넣어 예상 완료일과 일정 영향을 계산합니다.",
        "",
        "1. 'WBS' 시트: 진행률 기준일, 현재 계획 완료일, 작업별 담당자·계획 기간·진행률·선행 작업을 적습니다.",
        "2. '인력 투입 계획' 시트: 담당자별 월 투입률(1.0 = 한 달 전담, 0.5 = 절반)을 적습니다.",
        "3. '휴무' 시트: 공휴일·회사 휴무, 개인 휴가가 있을 때 적습니다.",
        "4. 프로젝트 상세 > 문서 기준 > 문서 추가에서 XLSX를 올리고, 문서 유형 '일정·공수 자료'로 등록한 뒤 '영향 산정 자료 사용 승인'을 켭니다.",
        "",
        "남은 시간은 이렇게 계산합니다",
        "- '남은 시간(h)' 칸을 채우면 그 값을 씁니다.",
        "- 비어 있으면 '계획 기간에 담당자가 쓸 수 있는 시간 × (1 − 진행률)'로 추정하고 0.5시간 단위로 올립니다.",
        "  예: 계획 10근무일, 투입률 1.0(하루 8시간), 진행률 70% → 80시간 × 0.3 = 24시간.",
        "- 진행률 100% 또는 실제 종료일이 있는 작업은 끝난 작업으로 보고 계산에서 뺍니다.",
        "- 하루 투입 시간 = 그 달 투입률 × 1 MD 기준 시간. 표의 마지막 달 이후에는 마지막 달 투입률이 이어진다고 봅니다.",
        "- 추정값이라는 사실은 산정 결과와 고객 안내 문서에 함께 표시됩니다. 진행률이 실제와 다르면 결과도 달라집니다.",
        "",
        "꼭 지킬 것",
        "- 변경 요청으로 새로 생길 작업은 적지 않습니다. Agent 3가 요청을 분석해 따로 만듭니다.",
        "- 작업명과 담당자 이름은 중복 없이 적습니다. Agent 3는 이 이름을 그대로 써서 새 작업의 담당자와 순서를 제안합니다.",
        "- '선행 작업'에는 WBS에 있는 작업명만 적고, 여러 개면 세미콜론(;)으로 구분합니다. 표에 없는 값(예: '확인 필요')이 있으면 파일을 읽지 않습니다.",
        "- WBS의 담당자(끝나지 않은 작업)는 모두 '인력 투입 계획'에 있어야 합니다.",
        "- 진행률은 70% 또는 0.7로 적습니다. 숫자 70은 허용하지 않습니다. 날짜는 2026-10-12처럼 적고 수식은 쓰지 않습니다.",
        "- 시트 이름, 제목 행, 열 순서는 바꾸지 않습니다. 행이 모자라면 입력 칸 사이에 행을 삽입합니다.",
        "",
        "계산되는 것과 안 되는 것",
        "- 현재 계획 완료일을 비우면 남은 작업의 가장 늦은 계획 종료일을 계획 완료일로 씁니다.",
        "- 계산기는 계획 시작일을 지키지 않고, 선행 작업이 끝나고 담당자 시간이 비면 바로 시작한다고 봅니다.",
        "- 휴무 시트가 비어 있으면 휴무가 없다고 보고 계산합니다.",
        "- 새 작업 중 하나라도 담당자가 정해지지 않으면 공수·금액만 산정하고 일정은 계산하지 않습니다.",
        "- 단가·금액은 이 파일이 아니라 시스템에 등록된 단가표로 계산합니다.",
    ]
    if example:
        lines += ["", "이 예시는 데이터셋의 모노마켓 커머스 리뉴얼(계약 9/1~11/23, 변경합의로 오픈 11/24)을 바탕으로 했습니다.",
                  "진행률·투입률·담당자 이름은 검증용 가정입니다. 기대 결과는 '예시 결과' 시트에 있습니다."]
    lines += ["", "입력 검증",
              "- 진행률·투입률은 0~1 또는 0%~100%입니다. 100% 초과는 거절합니다.",
              "- 실제 시작·종료일은 진행률 기준일 이후일 수 없고, 종료일은 시작일보다 빠를 수 없습니다.",
              "- 같은 담당자의 미완료 작업 기간이 겹치면 해당 작업의 '남은 시간(h)'을 직접 입력합니다.",
              "- 1 MD 기준 시간은 시스템의 공식 계산 기준과 같아야 합니다. 개인 가용시간은 월 투입률로 조절합니다.",
              "- 전체 일정 지연과 변경 요청으로 추가된 일정 영향을 구분합니다. 기존 지연을 없애려고 원래 공수를 줄이지 않습니다."]
    for i, text in enumerate(lines, 3):
        if text:
            ws.cell(i, 1, text).font = BOLD if text in ("남은 시간은 이렇게 계산합니다", "꼭 지킬 것", "계산되는 것과 안 되는 것") else Font(size=11)


def wbs_sheet(wb, data):
    ws = wb.create_sheet("WBS")
    widths = (30, 14, 12, 12, 12, 12, 9, 44, 13, 30)
    for i, width in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = width
    ws["A1"] = "WBS"
    ws["A1"].font = TITLE
    ws["A2"] = "WBS는 프로젝트의 작업, 담당자, 일정과 진행 상황을 정리한 표입니다."
    ws["A2"].font = NOTE
    ws["A3"] = "파란 칸: 매주 확인·갱신할 항목 · 노란 칸: 처음 작성하거나 변경 시 수정할 항목"
    ws["A3"].font = NOTE
    top = [("진행률 기준일", data and data["base"], DATE_FMT, "아래 진행률을 업데이트한 날짜를 꼭 입력해주세요. 남은 진행률을 이 날짜 기준으로 계산합니다."),
           ("현재 계획 완료일", data and data["planned_end"], DATE_FMT, "변경 전 계획의 완료일(오픈일). 비우면 가장 늦은 계획 종료일을 씁니다."),
           ("근무 요일", (data or {}).get("weekdays", "월,화,수,목,금"), None, "쉼표로 구분합니다.")]
    for r, (label, value, fmt, note) in enumerate(top, 4):
        ws.cell(r, 1, label).font = BOLD
        _input(ws, r, 2, value, fmt)
        ws.cell(r, 4, note).font = NOTE
    ws["B4"].fill = PatternFill("solid", fgColor="BFDBFE")
    ws["B4"].font = BOLD
    head = 8
    _head(ws, head, ["작업명", "담당자", "계획 시작일", "계획 종료일", "실제 시작일", "실제 종료일", "진행률",
                     "선행 작업 (세미콜론 구분)", "남은 시간(h) (알면 입력)", "비고"])
    first, last = head + 1, head + TASK_ROWS
    rows = (data or {}).get("tasks", [])
    for i, r in enumerate(range(first, last + 1)):
        values = rows[i] if i < len(rows) else (None,) * 10
        for col, value in enumerate(values, 1):
            _input(ws, r, col, value, DATE_FMT if 3 <= col <= 6 else PCT_FMT if col == 7 else None)
            if col in (5, 6, 7) and (not data or i < len(rows)):
                ws.cell(r, col).fill = WEEKLY
            ws.cell(r, col).alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = 36
    ws.row_dimensions[head].height = 42
    _validation(ws, "list", [f"B{first}:B{last}"], "'인력 투입 계획' 시트의 담당자를 선택해주세요.",
                formula1=f"'인력 투입 계획'!$A$7:$A${6 + (len(data['people']) if data else PEOPLE_ROWS)}")
    _validation(ws, "date", ["B4:B5", f"C{first}:F{last}"], "날짜(예: 2026-10-12)를 입력해주세요.", operator="greaterThan", formula1="1")
    _validation(ws, "decimal", [f"G{first}:G{last}"], "0%~100% 사이로 입력해주세요.", operator="between", formula1="0", formula2="1")
    _validation(ws, "decimal", [f"I{first}:I{last}"], "0 이상의 시간(h)을 입력해주세요.", operator="greaterThanOrEqual", formula1="0")
    _notes(ws, last + 2, [
        "※ 이 줄 아래는 읽지 않습니다.",
        "변경 요청으로 새로 생길 작업은 적지 않습니다. 끝난 작업은 진행률 100%로 두면 계산에서 빠집니다.",
        "남은 시간을 아는 작업만 '남은 시간(h)'에 적습니다. 나머지는 계획 기간과 진행률로 추정합니다.",
    ])
    ws.freeze_panes = ws.cell(first, 2)


def staffing_sheet(wb, data):
    ws = wb.create_sheet("인력 투입 계획")
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 10
    months = (data or {}).get("months", TEMPLATE_MONTHS)
    for i in range(len(months)):
        ws.column_dimensions[chr(67 + i)].width = 11
    ws.column_dimensions[chr(68 + len(months))].width = 60
    ws["A1"] = "인력 투입 계획"
    ws["A1"].font = TITLE
    ws["A2"] = "칸의 숫자는 투입률입니다. 1.0은 한 달 내내 전담, 0.5는 절반(하루 4시간 정도)입니다."
    ws["A2"].font = NOTE
    ws["A3"] = "1 MM(인월)은 한 사람이 한 달 일한 양으로, 보통 근무일 20~22일 정도입니다."
    ws["A3"].font = NOTE
    ws["A4"] = "1 MD 기준 시간(h)"
    ws["A4"].font = BOLD
    _input(ws, 4, 2, (data or {}).get("hpm", 8))
    ws["D4"] = "시스템의 공식 MD 기준과 같아야 합니다. 개인 가용시간은 월 투입률로 조절합니다."
    ws["D4"].font = NOTE
    _head(ws, 6, ["담당자", "직군"])
    for i, m in enumerate(months):
        c = _input(ws, 6, 3 + i, m or None)
        c.font, c.number_format = BOLD, "@"
    total_col = 4 + len(months)  # 월 열과 합계 사이의 빈 열은 월 제목 파싱 경계다.
    _head(ws, 6, ["총투입량(MM)"], start_col=total_col)
    ws.column_dimensions[chr(64 + total_col)].width = 20
    note_col = total_col + 2
    ws.cell(6, note_col, "월 칸에는 2026-09처럼 연-월을 적습니다. 빠진 달 없이 왼쪽부터 순서대로 적습니다.").font = NOTE
    ws.cell(7, note_col, "직군: PM, UX, FE, BE, QA 중 하나. 같은 직군이 여러 명이면 이름을 다르게 적습니다.").font = NOTE
    people = (data or {}).get("people", [])
    first, last = 7, 6 + (len(people) if data else PEOPLE_ROWS)
    for i, r in enumerate(range(first, last + 1)):
        name, role, rates = people[i] if i < len(people) else (None, None, [None] * len(months))
        _input(ws, r, 1, name)
        _input(ws, r, 2, role)
        for j, rate in enumerate(rates):
            _input(ws, r, 3 + j, rate, "0.0")
        ws.cell(r, total_col, f'=IF(A{r}="","",SUM(C{r}:{chr(66 + len(months))}{r}))').number_format = '0.0" MM"'
        ws.cell(r, total_col).fill = HEAD
    total_row = last + 1
    _head(ws, total_row, ["월별 투입규모 합계", "전담 환산"])
    for col in range(3, 3 + len(months)):
        ws.cell(total_row, col, f"=SUM({chr(64 + col)}{first}:{chr(64 + col)}{last})").number_format = "0.0"
    ws.cell(total_row, total_col, f"=SUM({chr(64 + total_col)}{first}:{chr(64 + total_col)}{last})").number_format = '0.0" MM"'
    end_col = chr(66 + len(months))
    _validation(ws, "list", [f"B{first}:B{last}"], "PM, UX, FE, BE, QA 중에서 선택해주세요.", formula1='"PM,UX,FE,BE,QA"')
    _validation(ws, "decimal", [f"C{first}:{end_col}{last}"], "0~1 사이 투입률을 입력해주세요(1.0 = 전담).",
                operator="between", formula1="0", formula2="1")
    _notes(ws, total_row + 2, ["※ 이 줄 아래는 읽지 않습니다.",
                          "실제 투입률이 계획과 다르면(겸무·회의 등) 실제 값으로 고칩니다. 계약 금액 계산에는 쓰지 않습니다.",
                          "MM 합계는 각 달 전체를 기준으로 한 계획 요약입니다. 월 중간 참여·휴가는 별도 확인하며 실제 시간·금액 계산에는 사용하지 않습니다.",
                          "예시의 김민서·박서연·이준호·최지훈·정수빈은 실제 팀원이 아닌 가상 이름입니다."])
    ws.freeze_panes = "C7"


def holiday_sheet(wb, data):
    ws = wb.create_sheet("휴무")
    for col, width in zip("ABCDE", (24, 14, 22, 30, 60)):
        ws.column_dimensions[col].width = width
    ws["A1"] = "휴무"
    ws["A1"].font = TITLE
    ws["A2"] = "공휴일·회사 휴무는 대상=전체, 시간=0. 반차·개인 휴가는 그 담당자의 그날 실제 투입 가능 시간을 적습니다."
    ws["A2"].font = NOTE
    _head(ws, 4, ["대상 (전체 또는 담당자)", "날짜", "그날 투입 가능 시간(h)", "사유 (선택)"])
    first, last = 5, 4 + HOLIDAY_ROWS
    rows = (data or {}).get("holidays", [])
    for i, r in enumerate(range(first, last + 1)):
        values = rows[i] if i < len(rows) else (None,) * 4
        for col, value in enumerate(values, 1):
            _input(ws, r, col, value, DATE_FMT if col == 2 else None)
    _validation(ws, "date", [f"B{first}:B{last}"], "날짜(예: 2026-10-09)를 입력해주세요.", operator="greaterThan", formula1="1")
    _validation(ws, "decimal", [f"C{first}:C{last}"], "0~24 사이 시간(h)을 입력해주세요.", operator="between", formula1="0", formula2="24")
    _notes(ws, last + 2, ["※ 이 줄 아래는 읽지 않습니다.",
                          "휴무 표가 비어 있으면 휴무가 없다고 보고 계산합니다. 사유만 적고 대상을 비운 행은 오류가 됩니다."])
    ws.freeze_panes = "A5"


def result_sheet(wb):
    ws = wb.create_sheet("예시 결과")
    for col, width in zip("ABCDEF", (38, 10, 16, 30, 26, 12)):
        ws.column_dimensions[col].width = width
    ws["A1"] = "예시 결과 (참고용, 업로드 시 읽지 않음)"
    ws["A1"].font = TITLE
    ws["A2"] = "변경 요청 CR-A-009 (10/12 접수): 런칭 이벤트에서 신규회원 쿠폰과 배송비 쿠폰을 한 주문에 함께 적용하고 싶습니다."
    ws["A3"] = "범위 판단: 쿠폰 중복 적용(A-F14)은 최초 계약 제외 범위 → 변경 견적 대상."
    _head(ws, 5, ["Agent 3 작업 행", "직군", "공수", "담당자 / 늘어나는 기존 작업", "선행 → 후속 작업", "추가 시간"])
    for i, (task, role, md, who, after, before, extends) in enumerate(CHANGE_LINES, 6):
        values = (task, role, f"{md:g} MD", who or f"{extends} (+{md * 8:g}h)",
                  " → ".join(filter(None, ["; ".join(after), "; ".join(before)])) or "-", f"{md * 8:g}h")
        for col, value in enumerate(values, 1):
            ws.cell(i, col, value).border = BOX
            ws.cell(i, col).alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[i].height = 60
    row = 6 + len(CHANGE_LINES) + 1
    _head(ws, row, ["항목", "값"])
    e = EXPECTED
    expected = [("현재 계획 완료일", "2026-11-24"), ("기존 작업만의 완료일", e["baseline_end"]),
                ("변경 작업 시작일", e["change_start"]), ("예상 완료일", e["changed_end"]),
                ("일정 영향 (계획 대비)", f"{e['impact']}영업일"),
                ("일정 영향 (기존 작업만 대비)", f"{e['impact_vs_baseline']}영업일"),
                ("추가 공수", f"{e['change_md']} MD ({e['change_hours']}h)")]
    for i, (k, v) in enumerate(expected, row + 1):
        ws.cell(i, 1, k).border = BOX
        ws.cell(i, 2, v).border = BOX
    _notes(ws, row + len(expected) + 2, [
        "남은 시간 추정 예: 로그인·상품 목록/상세 API는 계획 9/14~10/8 중 근무일 16일 × 8시간 = 128시간, 진행률 90% → 12.8시간 → 13시간.",
        "결함 수정(BE·FE)은 '남은 시간(h)'에 24시간을 직접 적은 예입니다.",
        "정수빈는 10월 투입률 0.5(하루 4시간), 11월 1.0(하루 8시간)으로 달마다 다르게 계산됩니다.",
        "일정 영향은 계획 완료일(11/24) 다음 날부터 예상 완료일까지의 근무일 수입니다.",
        "기존 작업만으로는 계획대로 11/24에 끝납니다. 쿠폰 작업(BE 11/9~11/11, FE 11/11)이 끝나야 통합 테스트를 시작하고,",
        "통합 테스트가 12시간 늘어 11/25에 끝나므로 검수·최종 인수가 11/26~12/2로 밀립니다(11/25~12/2 = 6영업일).",
        "추가 공수(MD)와 일정 영향(영업일)은 다른 값입니다. 계산 규칙은 docs/agent3/schedule-calculator.md를 봅니다.",
        "기존 지연 검증 사례는 검수 남은 시간 28h를 유지합니다. 기존 완료 11/26, 변경 후 12/4: 전체 지연 8영업일, 변경 추가 영향 6영업일.",
    ])


def rate_card_sheet(wb, example):
    """등록 API의 입력을 모으는 작성용 시트. 업로드 자동 등록은 아직 연결하지 않는다."""
    ws = wb.create_sheet("단가표")
    for col, width in zip("ABCDE", (20, 24, 23, 23, 27)):
        ws.column_dimensions[col].width = width
    ws["A1"], ws["A1"].font = "단가표", TITLE
    _notes(ws, 2, [
        "각 직군의 1인 1일(1MD) 기준 단가를 입력합니다. 선택한 통화 기준이며 부가세는 제외합니다.",
        "내부 원가는 회사 부담 비용, 판매 단가는 고객에게 제안하는 금액입니다.",
    ])
    for row, (label, value, fmt, note) in enumerate([
        ("적용 시작일", D(2026, 9, 1) if example else None, DATE_FMT, "필수 · 새 단가가 적용되는 날짜"),
        ("1 MD 기준 시간(h)", "='인력 투입 계획'!B4", "0.0", "인력 투입 계획의 기준 시간과 자동 연동"),
        ("통화", "KRW", None, "프로젝트 통화와 같은 값 (예: KRW)"),
    ], 4):
        ws.cell(row, 1, label).font = BOLD
        _input(ws, row, 2, value, fmt)
        ws.cell(row, 4, note).font = NOTE
        ws.row_dimensions[row].height = 24
    _head(ws, 8, ["직군 코드", "직군명", "내부 원가 (1MD)", "판매 단가 (1MD)", "비고"])
    ws.row_dimensions[8].height = 36
    card_id = "RC-SYNTH-2026H2-v2"
    rates = json.loads((OUT.parent / "kb/rate_cards.json").read_text(encoding="utf-8"))["rate_cards"][card_id]["rates"] if example else {}
    labels = [("PM", "프로젝트 관리"), ("UX", "UX/UI 디자인"), ("FE", "프론트엔드 개발"),
              ("BE", "백엔드 개발"), ("QA", "품질 검증·테스트")]
    for row, (code, name) in enumerate(labels, 9):
        rate = rates.get(code, {})
        for col, value in enumerate((code, name, rate.get("cost"), rate.get("price"), None), 1):
            _input(ws, row, col, value, "#,##0.00" if col in (3, 4) else None)
        ws.row_dimensions[row].height = 30
    _validation(ws, "date", ["B4"], "적용 시작일을 날짜로 입력해주세요.", operator="greaterThan", formula1="1")
    _validation(ws, "decimal", ["C9:D13"], "0 이상의 단가를 입력해주세요.", operator="greaterThanOrEqual", formula1="0")
    notes = [
        "작성한 단가는 현재 자동 등록되지 않습니다. 금액 산정에는 시스템에 등록된 단가표가 적용됩니다.",
        f"출처: data/kb/rate_cards.json의 {card_id}. 실제 회사 단가가 아닌 합성 테스트 예시입니다." if example else
        "PM·UX·FE·BE·QA 다섯 직군을 모두 작성합니다. 금액 빈칸은 미입력이며 0원과 다릅니다.",
        "적용 시작일 2026-09-01은 모노마켓 예시용 가정입니다. 실제 등록된 단가표를 조회한 값은 아닙니다." if example else
        "직군 코드는 그대로 사용하고, 노란 칸의 적용 시작일·직군별 원가·판매 단가를 입력해주세요.",
    ]
    for row, text in enumerate(notes, 15):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        ws.cell(row, 1, text).font = NOTE
        ws.cell(row, 1).alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[row].height = 32
    note_row = 44
    _notes(wb["작성 안내"], note_row, [
        "단가표 작성",
        "단가표 시트에 적용 시작일·통화·직군별 원가와 판매 단가를 적습니다. 1 MD 기준 시간은 인력 투입 계획과 연동합니다.",
        "현재 업로드는 이 시트의 단가를 자동 등록하지 않습니다. 공식 금액은 시스템에 등록된 프로젝트 단가표로 계산합니다.",
    ])
    wb["작성 안내"].cell(note_row, 1).font = BOLD


def build(example: bool) -> Workbook:
    wb = Workbook()
    guide_sheet(wb, example)
    data = EXAMPLE if example else None
    wbs_sheet(wb, data)
    staffing_sheet(wb, data)
    holiday_sheet(wb, data)
    if example:
        result_sheet(wb)
    rate_card_sheet(wb, example)
    return wb


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, example in (("impact-workbook-template.xlsx", False), ("impact-workbook-example.xlsx", True)):
        build(example).save(OUT / name)
        print(OUT / name)


if __name__ == "__main__":
    main()
