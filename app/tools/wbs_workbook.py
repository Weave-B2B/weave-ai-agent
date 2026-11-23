"""WBS·인력 투입 계획 엑셀(wbs-v1)을 일정 계산 입력 구조로 바꾼다.

SI 현장에서 이미 쓰는 자료(작업별 계획 기간·진척률, 사람별 월 투입률)를 그대로 받는다.
- 남은 시간은 직접 적은 값이 있으면 그 값, 없으면 `계획 기간에 담당자가 쓸 수 있는 시간 × (1 − 진척률)`로
  추정하고 0.5시간 단위로 올린다. 추정이라는 사실을 assumptions에 남긴다.
- 하루 투입 시간은 `월 투입률 × 1 MD 기준 시간`이다. 달마다 투입률이 다르면 일정 계산 요청을 만들 때
  담당자별 예외 근무일로 펼친다(expand_staffing). 표의 마지막 달 이후는 마지막 달 투입률이 이어진다고 본다.
결과 구조는 간소화 양식(parse_workbook)과 같고, staffing·estimates·assumptions가 더해진다.
"""
from __future__ import annotations

import math
import re
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from app.tools.errors import ToolInputError

FORMAT = "wbs-v1"
SHEETS = ("WBS", "인력 투입 계획")
_WEEKDAYS = {"월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 7}
MAX_TASKS, MAX_PEOPLE, MAX_EXCEPTIONS, MAX_MONTHS = 200, 200, 1000, 36


def is_wbs(sheets: dict) -> bool:
    return SHEETS[0] in sheets


def _cell(cells, col):
    value, formula = cells.get(col, ("", False))
    if formula:
        raise ToolInputError(f"{col}열 입력에 수식이 있습니다. 값으로 입력해주세요.")
    return str(value).strip()


def _column(index):
    """0부터 세는 열 번호 → A, B, ..., Z, AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _key(text):
    return re.sub(r"\s+", "", text)


def _number(value, label, max_value=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ToolInputError(f"{label}: 숫자를 입력해주세요.") from None
    if not math.isfinite(n) or n < 0 or (max_value is not None and n > max_value):
        raise ToolInputError(f"{label}: 허용 범위 밖의 값입니다.")
    return n


def _decimal(value, label, max_value=None):
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise ToolInputError(f"{label}: 숫자를 입력해주세요.") from None
    if not n.is_finite() or n < 0 or (max_value is not None and n > max_value):
        raise ToolInputError(f"{label}: 허용 범위 밖의 값입니다.")
    return n


def _date(value, label, required=False):
    if not value:
        if required:
            raise ToolInputError(f"{label}을 입력해주세요.")
        return None
    try:
        if re.fullmatch(r"\d+(\.0+)?", value):
            return date(1899, 12, 30) + timedelta(days=int(float(value)))
        return date.fromisoformat(value)
    except (ValueError, OverflowError):
        raise ToolInputError(f"{label}: 날짜 형식이 올바르지 않습니다.") from None


def _ratio(value, label):
    """0~1 또는 %를 명시한 값만 받는다. 단위 없는 70을 70%로 보정하지 않는다."""
    if value == "":
        return 0.0
    percentage = value.endswith("%")
    text = value[:-1].strip() if percentage else value
    n = _decimal(text, label, 100 if percentage else 1)
    return float(n / 100 if percentage else n)


def _month(value, label):
    m = re.fullmatch(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*월?(?:[-./]\d{1,2})?", value)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    if re.fullmatch(r"\d+(\.0+)?", value):
        d = _date(value, label)
        return d.year, d.month
    raise ToolInputError(f"{label}: 2026-09처럼 연-월로 입력해주세요.")


def workdays(text):
    days = [_WEEKDAYS.get(d) for d in re.split(r"[,\s]+", text or "") if d]
    if not days or None in days or len(days) != len(set(days)):
        raise ToolInputError("근무요일은 월,화,수,목,금 형태로 입력해주세요.")
    return set(days)


class Capacity:
    """담당자별 날짜별 투입 가능 시간. Backend 계산기의 규칙(개인 예외 > 전체 예외와 기본값 중 작은 값 > 근무요일)과 같다."""

    def __init__(self, staffing, calendar):
        self.hpm = Decimal(str(staffing["hours_per_md"]))
        self.months = [tuple(map(int, m.split("-"))) for m in staffing["months"]]
        self.rates = staffing["rates"]
        self.workdays = workdays(calendar["working_weekdays"])
        self.overall, self.personal = {}, {}
        for e in calendar.get("exceptions", []):
            d = date.fromisoformat(e["date"])
            if e["person"] == "전체":
                self.overall[d] = Decimal(str(e["hours"]))
            else:
                self.personal[(e["person"], d)] = Decimal(str(e["hours"]))

    def rate(self, person, d):
        m = (d.year, d.month)
        index = next((i for i, x in enumerate(self.months) if x == m), None)
        if index is None:
            index = 0 if m < self.months[0] else len(self.months) - 1
        return Decimal(str(self.rates[person][index]))

    def default(self, d, daily):
        if d in self.overall:
            return min(self.overall[d], daily)
        return daily if d.isoweekday() in self.workdays else Decimal(0)

    def hours(self, person, d):
        if (person, d) in self.personal:
            return self.personal[(person, d)]
        return self.default(d, self.rate(person, d) * self.hpm)

    def last_month_end(self):
        y, m = self.months[-1]
        return date(y, m, monthrange(y, m)[1])


def expand_staffing(data):
    """일정 계산 요청용 (담당자별 기본 하루 시간, 달별 투입률을 펼친 개인 예외 근무일)."""
    staffing = data.get("staffing")
    if not staffing:
        return None, []
    cap = Capacity(staffing, data["calendar"])
    start = date.fromisoformat(data["calendar"]["start_date"])
    base = {p: Decimal(str(rates[-1])) * cap.hpm for p, rates in staffing["rates"].items()}
    derived = []
    for person, daily in base.items():
        d = start
        while d <= cap.last_month_end():
            if (person, d) not in cap.personal:
                want, backend = cap.hours(person, d), cap.default(d, daily)
                if want != backend:
                    derived.append({"person": person, "date": d.isoformat(), "hours": float(want)})
            d += timedelta(days=1)
    return {p: float(daily) for p, daily in base.items()}, derived


def _half_up(hours):
    return float((Decimal(str(hours)) * 2).to_integral_value(rounding=ROUND_CEILING) / 2)


def _top(rows, labels, stop):
    """표 제목 행(stop) 위쪽의 '항목 | 값' 행만 읽는다."""
    found = {}
    for _, cells in rows:
        key = _key(_cell(cells, "A"))
        if key == stop:
            break
        for name, prefix in labels.items():
            if key.startswith(prefix):  # prefix는 문자열 또는 기존·새 표기의 튜플
                found[name] = _cell(cells, "B")
    return found


def _table(rows, is_header, label, stop_labels=()):
    """제목 행 다음부터 '※'로 시작하는 행 전까지. 빈 행은 건너뛴다."""
    header, body = None, []
    for row, cells in rows:
        first = _cell(cells, "A")
        if header is None:
            if is_header(first, cells):
                header = cells
            continue
        if first.startswith("※") or first in stop_labels:
            break
        body.append((row, cells))
    if header is None:
        raise ToolInputError(f"{label} 시트의 제목 행(열 구성)을 찾을 수 없습니다.")
    return header, body


def _canonical_wbs_columns(head, rows):
    """보기용 직군 열(C)이 있는 양식도 기존 작업·날짜 입력 계약으로 정규화한다.

    직군 표시 수식은 읽거나 실행하지 않는다. 실제 인력의 직군은 인력 투입 계획에서 읽는다.
    """
    if _key(_cell(head, "C")) != "직군":
        return head, rows

    def convert(cells):
        return {_column(i): cells.get(_column(i if i < 2 else i + 1), ("", False))
                for i in range(10)}

    return convert(head), [(row, convert(cells)) for row, cells in rows]


def parse(sheets: dict) -> dict:
    if not set(SHEETS) <= sheets.keys():
        raise ToolInputError("WBS / 인력 투입 계획 시트가 필요합니다.")
    top = _top(sheets["WBS"], {"base": ("진척률기준일", "진행률기준일"), "planned_end": "현재계획완료일", "weekdays": "근무요일"}, "작업명")
    start = _date(top.get("base", ""), "진행률 기준일", True)
    planned_end = _date(top.get("planned_end", ""), "현재 계획 완료일")
    weekdays = top.get("weekdays") or ""
    workdays(weekdays)

    staff_top = _top(sheets["인력 투입 계획"], {"hpm": "1MD기준시간"}, "담당자")
    hpm = _number(staff_top.get("hpm") or "8", "1 MD 기준 시간", 24)
    if hpm == 0:
        raise ToolInputError("1 MD 기준 시간은 0보다 커야 합니다.")
    head, rows = _table(sheets["인력 투입 계획"], lambda a, c: a == "담당자" and "직군" in _cell(c, "B"),
                        "인력 투입 계획", stop_labels=("월별 투입규모 합계",))
    cols = []  # C열부터 빈 칸 전까지가 월 열이다(오른쪽 안내 문구는 빈 열 하나를 띄운다)
    for i in range(2, 2 + MAX_MONTHS + 1):
        col = _column(i)
        if not _cell(head, col):
            break
        cols.append(col)
    if not cols or len(cols) > MAX_MONTHS:
        raise ToolInputError("인력 투입 계획에 월 열(예: 2026-09)이 필요합니다.")
    months = [_month(_cell(head, c), f"인력 투입 계획 {c}열 제목") for c in cols]
    if months != sorted(set(months)) or any((b[0] * 12 + b[1]) - (a[0] * 12 + a[1]) != 1 for a, b in zip(months, months[1:])):
        raise ToolInputError("인력 투입 계획의 월은 빠짐없이 순서대로 적어주세요.")
    people, rates = [], {}
    for row, cells in rows:
        name = _cell(cells, "A")
        if not name:
            if any(_cell(cells, c) for c in ["B", *cols]):
                raise ToolInputError(f"인력 투입 계획 {row}행: 담당자가 없습니다.")
            continue
        if name in rates:
            raise ToolInputError(f"인력 투입 계획 {row}행: 담당자가 중복되었습니다.")
        rates[name] = [_ratio(_cell(cells, c), f"인력 투입 계획 {row}행 {c}열 투입률") for c in cols]
        people.append({"name": name, "role": _cell(cells, "B")})

    exceptions = []
    if "휴무" in sheets:
        _, rows = _table(sheets["휴무"], lambda a, c: a.startswith("대상"), "휴무")
        for row, cells in rows:
            who = _cell(cells, "A")
            if not who:
                if any(_cell(cells, c) for c in "BCD"):
                    raise ToolInputError(f"휴무 {row}행: 대상이 없습니다.")
                continue
            if who != "전체" and who not in rates:
                raise ToolInputError(f"휴무 {row}행: 인력 투입 계획에 없는 담당자입니다.")
            exceptions.append({"person": who, "date": _date(_cell(cells, "B"), f"휴무 {row}행 날짜", True).isoformat(),
                               "hours": _number(_cell(cells, "C"), f"휴무 {row}행 투입 가능 시간", 24)})
    if len({(e["person"], e["date"]) for e in exceptions}) != len(exceptions):
        raise ToolInputError("휴무 날짜가 중복되었습니다.")
    if len(people) > MAX_PEOPLE or len(exceptions) > MAX_EXCEPTIONS:
        raise ToolInputError("인력은 200행, 휴무는 1000행까지 지원합니다.")

    calendar = {"start_date": start.isoformat(), "planned_end": None, "working_weekdays": weekdays, "exceptions": exceptions}
    staffing = {"hours_per_md": hpm, "months": [f"{y}-{m:02d}" for y, m in months], "rates": rates}
    cap = Capacity(staffing, calendar)

    head, rows = _table(sheets["WBS"], lambda a, c: a == "작업명", "WBS")
    head, rows = _canonical_wbs_columns(head, rows)
    for words, col in [(('담당자',), "B"), (('계획 시작',), "C"), (('계획 종료',), "D"),
                       (('실제 시작', '실적 시작'), "E"), (('실제 종료', '실적 종료'), "F"),
                       (('진행', '진척'), "G"), (('선행',), "H"), (('남은',), "I")]:
        if not any(word.replace(" ", "") in _key(_cell(head, col)) for word in words):
            raise ToolInputError("WBS 시트의 열 구성이 다릅니다.")
    raw = []
    for row, cells in rows:
        name = _cell(cells, "A")
        if not name:
            if any(_cell(cells, c) for c in "BCDEFGHI"):
                raise ToolInputError(f"WBS {row}행: 작업명이 없습니다.")
            continue
        raw.append((row, name, cells))
    names = [n for _, n, _ in raw]
    if not raw or len(raw) > MAX_TASKS:
        raise ToolInputError("WBS 작업은 1~200행이어야 합니다.")
    if len(names) != len(set(names)):
        raise ToolInputError("WBS 작업명은 중복될 수 없습니다.")

    tasks, estimates, done, periods = [], [], set(), []
    for row, name, cells in raw:
        owner = _cell(cells, "B")
        progress = _ratio(_cell(cells, "G"), f"WBS {row}행 진행률")
        actual_start = _date(_cell(cells, "E"), f"WBS {row}행 실제 시작일")
        finished = _date(_cell(cells, "F"), f"WBS {row}행 실제 종료일")
        if any(d is not None and d > start for d in (actual_start, finished)):
            raise ToolInputError(f"WBS {row}행: 실제 시작·종료일은 진행률 기준일 이후일 수 없습니다.")
        if actual_start and finished and finished < actual_start:
            raise ToolInputError(f"WBS {row}행: 실제 종료일은 실제 시작일보다 빠를 수 없습니다.")
        direct = _cell(cells, "I")
        plan_start = _date(_cell(cells, "C"), f"WBS {row}행 계획 시작일", not direct and progress < 1)
        plan_end = _date(_cell(cells, "D"), f"WBS {row}행 계획 종료일", not direct and progress < 1)
        if plan_start and plan_end and (plan_end < plan_start or (plan_end - plan_start).days > 1100):
            raise ToolInputError(f"WBS {row}행: 계획 기간을 확인해주세요.")
        preds = [x.strip() for x in re.split(r"[;\n]", _cell(cells, "H")) if x.strip()]
        unknown = [p for p in preds if p not in names]
        if unknown or name in preds:
            raise ToolInputError(f"WBS {row}행: 선행 작업을 확인해주세요({', '.join(unknown) or name}). 표에 있는 작업명만 적습니다.")
        if finished or progress >= 1:
            if progress < 1:
                raise ToolInputError(f"WBS {row}행: 실제 종료일이 있으면 진행률은 100%여야 합니다.")
            if direct and _number(direct, f"WBS {row}행 남은 시간") > 0:
                raise ToolInputError(f"WBS {row}행: 완료된 작업에 남은 시간이 있습니다.")
            done.add(name)
            estimates.append({"task": name, "basis": "done", "progress": 1.0, "remaining_hours": 0.0})
            continue
        if not owner:
            raise ToolInputError(f"WBS {row}행: 담당자가 없습니다.")
        if owner not in rates:
            raise ToolInputError(f"WBS {row}행: 인력 투입 계획에 없는 담당자입니다({owner}).")
        periods.append((row, name, owner, plan_start, plan_end, bool(direct)))
        if direct:
            remaining = _number(direct, f"WBS {row}행 남은 시간")
            estimates.append({"task": name, "basis": "direct", "progress": progress, "remaining_hours": remaining})
        else:
            planned = sum((cap.hours(owner, plan_start + timedelta(days=i)) for i in range((plan_end - plan_start).days + 1)), Decimal(0))
            if planned <= 0:
                raise ToolInputError(f"WBS {row}행: 계획 기간에 {owner}의 투입 시간이 없어 남은 시간을 추정할 수 없습니다. 남은 시간을 직접 입력해주세요.")
            remaining = _half_up(planned * (Decimal(1) - Decimal(str(progress))))
            estimates.append({"task": name, "basis": "progress", "progress": progress,
                              "planned_hours": float(planned), "remaining_hours": remaining})
        tasks.append({"name": name, "owner": owner, "remaining_hours": remaining, "predecessors": preds,
                      "plan_end": plan_end and plan_end.isoformat()})
    # 월 투입률은 담당자 전체의 가용시간이다. 겹친 각 작업에 전부 중복 배정하지 않는다.
    for row, name, owner, begin, end, direct in periods:
        if direct or begin is None or end is None:
            continue
        overlapping = [other for _, other, who, a, b, _ in periods
                       if other != name and who == owner and a and b
                       and max(begin, a) <= min(end, b)]
        if overlapping:
            raise ToolInputError(f"WBS {row}행: {owner}의 계획 기간이 다른 작업과 겹칩니다({', '.join(overlapping)}). "
                                 f"{name}의 남은 시간을 직접 입력해주세요.")
    for t in tasks:
        t["predecessors"] = [p for p in t["predecessors"] if p not in done]
    if not tasks:
        raise ToolInputError("남은 작업이 없습니다. 진행률·실제 종료일을 확인해주세요.")
    graph = {t["name"]: t["predecessors"] for t in tasks}
    seen, active = set(), set()

    def visit(n):
        if n in active:
            raise ToolInputError("선행 작업이 순환합니다.")
        if n in seen:
            return
        active.add(n)
        for p in graph[n]:
            visit(p)
        active.discard(n)
        seen.add(n)
    for n in graph:
        visit(n)

    assumptions = [f"진척률 기준일 {start.isoformat()}의 WBS 진척률로 남은 시간을 추정했습니다."]
    estimated = [e for e in estimates if e["basis"] == "progress"]
    if estimated:
        assumptions.append(f"작업 {len(estimated)}건의 남은 시간은 계획 기간의 투입 가능 시간 × (1 − 진척률)로 추정해 0.5시간 단위로 올렸습니다.")
    assumptions.append(f"하루 투입 시간 = 월 투입률 × {hpm:g}시간. {staffing['months'][-1]} 이후는 같은 투입률이 이어진다고 가정합니다.")
    plan_ends = [t.pop("plan_end") for t in tasks]
    if planned_end is None:
        ends = [e for e in plan_ends if e]
        if ends:
            planned_end = date.fromisoformat(max(ends))
            assumptions.append(f"현재 계획 완료일이 비어 있어 남은 작업의 가장 늦은 계획 종료일({planned_end.isoformat()})을 썼습니다.")
    calendar["planned_end"] = planned_end and planned_end.isoformat()
    start_rates = {p: float(cap.rate(p, start) * cap.hpm) for p in rates}
    return {
        "format": FORMAT,
        "tasks": tasks,
        "people": [{"name": p["name"], "role": p["role"], "daily_hours": start_rates[p["name"]],
                    "available_from": start.isoformat()} for p in people],
        "calendar": calendar,
        "staffing": staffing,
        "estimates": estimates,
        "assumptions": assumptions,
        "missing": [] if planned_end else ["현재 계획 완료일 미입력: 지연 폭 미산정"],
        "note": "업로드된 운영 자료(WBS·인력 투입 계획). 계약 근거 아님. 남은 시간은 진척률 기반 추정을 포함. "
                "금액은 Backend 등록 단가표, 완료일·일정 영향은 Backend 일정 계산기가 계산.",
    }
